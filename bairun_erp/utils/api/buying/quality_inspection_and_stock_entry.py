# Copyright (c) 2025, Bairun and contributors
# 质检 + 入库 一体化白名单接口：根据采购接收单行创建质检单，质检通过则自动创建并提交入库（Stock Entry Material Transfer）。
#
# 逻辑简述：
# 1. 根据 purchase_receipt + pr_item_name 找到 PR 行，创建 Quality Inspection（含自定义字段：订单数量、良品/次品数量、次品处理意见、次品照片），并提交。
# 2. 若质检状态为 Accepted 且良品数量 > 0 且传入了 to_warehouse，则创建一张 Stock Entry（Material Transfer），从 PR 行仓库转入 to_warehouse，并提交。
#
# bench execute 示例:
#   bench --site site2.local execute bairun_erp.utils.api.buying.quality_inspection_and_stock_entry.submit_quality_inspection_and_stock_entry --kwargs '{"purchase_receipt": "MAT-PRE-2026-00001", "pr_item_name": "al44lq3fib", "order_qty": 1, "good_qty": 1, "defective_qty": 0, "to_warehouse": "毛胚 - B"}'

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, getdate


def _get_pr_item_row(purchase_receipt, pr_item_name):
	"""获取采购接收单指定子表行，不存在或 PR 未提交则抛错。"""
	pr = frappe.get_doc("Purchase Receipt", purchase_receipt)
	if pr.docstatus != 1:
		frappe.throw(_("采购接收单 {0} 未提交").format(purchase_receipt))
	for row in pr.items:
		if row.name == pr_item_name:
			return pr, row
	frappe.throw(_("采购接收单 {0} 中未找到行 {1}").format(purchase_receipt, pr_item_name))


@frappe.whitelist()
def submit_quality_inspection_and_stock_entry(
	purchase_receipt,
	pr_item_name,
	good_qty=0,
	defective_qty=0,
	order_qty=None,
	defective_handling=None,
	defective_photos=None,
	to_warehouse=None,
	work_instruction_guide=None,
):
	"""
	创建并提交质检单；若质检通过且良品数量>0 且 to_warehouse 有值，则创建并提交入库（Material Transfer）Stock Entry。

	:param purchase_receipt: 采购接收单号（Purchase Receipt name）
	:param pr_item_name: 采购接收单行 name（Purchase Receipt Item 的 name，如 al44lq3fib）
	:param good_qty: 良品数量
	:param defective_qty: 次品数量
	:param order_qty: 订单数量（可选，不传则用 PR 行 qty）
	:param defective_handling: 次品处理意见（可选）：退回 / 报废 / 特采
	:param defective_photos: 次品照片附件，支持多张：传列表 ["url1", "url2"] 或单张 "url"
	:param to_warehouse: 入库目标仓库；有值且质检通过、良品>0 时才会创建 Stock Entry
	:param work_instruction_guide: 作业指导书附件 URL（可选）
	:return: {"quality_inspection": "<name>", "status": "Accepted"|"Rejected", "stock_entry": "<name>"|None}
	"""
	good_qty = flt(good_qty, 6)
	defective_qty = flt(defective_qty, 6)
	order_qty = flt(order_qty, 6) if order_qty is not None else None

	pr, pr_row = _get_pr_item_row(purchase_receipt, pr_item_name)
	sample_size = order_qty if order_qty is not None else flt(pr_row.get("qty"), 6)
	if sample_size <= 0:
		sample_size = 1.0

	# 1. 创建并提交质检单
	qi = frappe.new_doc("Quality Inspection")
	qi.reference_type = "Purchase Receipt"
	qi.reference_name = purchase_receipt
	qi.child_row_reference = pr_item_name
	qi.inspection_type = "Incoming"
	qi.report_date = getdate()
	qi.company = pr.company
	qi.item_code = pr_row.item_code
	qi.item_name = pr_row.get("item_name")
	qi.description = pr_row.get("description")
	qi.sample_size = sample_size
	qi.manual_inspection = 1
	qi.inspected_by = frappe.session.user

	# 自定义字段（质检结果 / 次品处理 / 照片）
	if work_instruction_guide and frappe.get_meta("Quality Inspection").has_field("custom_work_instruction_guide"):
		qi.custom_work_instruction_guide = work_instruction_guide
	if frappe.get_meta("Quality Inspection").has_field("custom_order_qty"):
		qi.custom_order_qty = sample_size
	if frappe.get_meta("Quality Inspection").has_field("custom_good_qty"):
		qi.custom_good_qty = good_qty
	if frappe.get_meta("Quality Inspection").has_field("custom_defective_qty"):
		qi.custom_defective_qty = defective_qty
	if defective_handling and frappe.get_meta("Quality Inspection").has_field("custom_defective_handling"):
		qi.custom_defective_handling = defective_handling
	# 次品照片：支持多张，写入子表 custom_defective_photos（QI Defective Photo）
	if defective_photos and frappe.get_meta("Quality Inspection").has_field("custom_defective_photos"):
		meta = frappe.get_meta("Quality Inspection")
		cf = meta.get_field("custom_defective_photos")
		if cf and cf.fieldtype == "Table" and cf.options == "QI Defective Photo":
			urls = defective_photos if isinstance(defective_photos, (list, tuple)) else [defective_photos]
			for url in urls:
				if url:
					qi.append("custom_defective_photos", {"photo": url})
		else:
			qi.custom_defective_photos = defective_photos

	qi.status = "Rejected" if defective_qty > 0 else "Accepted"
	qi.insert(ignore_permissions=True)
	qi.submit()

	out = {"quality_inspection": qi.name, "status": qi.status, "stock_entry": None}

	# 2. 质检通过且良品>0 且指定了目标仓：创建入库 Stock Entry（Material Transfer）
	if qi.status != "Accepted" or good_qty <= 0 or not to_warehouse:
		return out

	s_warehouse = pr_row.get("warehouse")
	if not s_warehouse:
		frappe.throw(_("采购接收单行未设置仓库，无法生成入库单"))
	if s_warehouse == to_warehouse:
		# 同仓不建转移单（避免校验报错）
		return out

	if not frappe.db.exists("Warehouse", to_warehouse):
		frappe.throw(_("目标仓库 {0} 不存在").format(to_warehouse))

	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	rate = flt(pr_row.get("base_rate") or pr_row.get("rate"), 6)
	cost_center = pr_row.get("cost_center") or frappe.get_cached_value("Company", pr.company, "cost_center")
	se = make_stock_entry(
		item_code=pr_row.item_code,
		qty=good_qty,
		from_warehouse=s_warehouse,
		to_warehouse=to_warehouse,
		company=pr.company,
		rate=rate,
		cost_center=cost_center,
		do_not_save=False,
		do_not_submit=True,
	)
	if se.get("items") and len(se.items) > 0:
		se.items[0].reference_purchase_receipt = purchase_receipt
	se.save(ignore_permissions=True)
	se.submit()
	out["stock_entry"] = se.name
	return out
