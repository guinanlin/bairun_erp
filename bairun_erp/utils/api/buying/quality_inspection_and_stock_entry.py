# Copyright (c) 2025, Bairun and contributors
# 质检 + 入库 一体化白名单接口：根据采购接收单行创建质检单，质检通过则自动创建并提交入库（Stock Entry Material Receipt）。
#
# 逻辑简述：
# 1. 根据 purchase_receipt + pr_item_name 找到 PR 行，创建 Quality Inspection（含自定义字段：订单数量、良品/次品数量、次品处理意见、次品照片），并提交。
# 2. 若质检状态为 Accepted 且良品数量 > 0 且传入了 to_warehouse，则创建一张 Stock Entry（Material Receipt 采购收货），将良品数量入库到 to_warehouse，并提交。
#    入库单类型固定为 Material Receipt，不使用 Material Transfer；只要 to_warehouse 有值且质检通过、良品>0 即建单并返回入库单号。
#    同时从 PR 行带出 purchase_order 写入 SE 表头及子表行：表头供报表/API，子表行供 SE 的 Connections 页显示 PO。
# 3. 若 PR 行有 purchase_order 但缺少 purchase_order_item，会补全并触发 PR 的 update_prevdoc_status，使 PO 的 received_qty / per_received 正确回写。
#
# bench execute 示例:
#   bench --site site2.local execute bairun_erp.utils.api.buying.quality_inspection_and_stock_entry.submit_quality_inspection_and_stock_entry --kwargs '{"purchase_receipt": "MAT-PRE-2026-00001", "pr_item_name": "al44lq3fib", "order_qty": 1, "good_qty": 1, "defective_qty": 0, "to_warehouse": "毛胚 - B"}'

from __future__ import unicode_literals

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate


def analyze_po_pr_se_flow(po_name):
	"""
	分析一张采购订单（PO）与下游采购收货（PR）、入库单（SE）的流转关系，供 bench execute 调用。
	用法: bench --site site2.local execute bairun_erp.utils.api.buying.quality_inspection_and_stock_entry.analyze_po_pr_se_flow --args '["PUR-ORD-2026-00001"]'
	"""
	out = {"purchase_order_name": po_name, "purchase_order": None, "purchase_receipts": [], "stock_entries": []}
	if not frappe.db.exists("Purchase Order", po_name):
		out["error"] = "Purchase Order not found"
		return out

	po = frappe.get_doc("Purchase Order", po_name)
	out["purchase_order"] = {
		"name": po.name,
		"supplier": po.supplier,
		"company": po.company,
		"docstatus": po.docstatus,
		"status": getattr(po, "status", None),
		"posting_date": str(po.posting_date) if po.get("posting_date") else None,
		"items": [
			{"idx": d.idx, "name": d.name, "item_code": d.item_code, "qty": d.qty}
			for d in (po.items or [])
		],
	}

	# PR: 通过 Purchase Receipt Item.purchase_order 关联到 PO
	pr_rows = frappe.get_all(
		"Purchase Receipt Item",
		filters={"purchase_order": po_name},
		fields=["parent", "idx", "name", "item_code", "qty", "purchase_order", "purchase_order_item", "warehouse"],
	)
	pr_names = list({r.parent for r in pr_rows})
	for pr_name in pr_names:
		pr_header = frappe.db.get_value(
			"Purchase Receipt",
			pr_name,
			["name", "supplier", "company", "docstatus", "posting_date"],
			as_dict=True,
		)
		if pr_header:
			pr_header["posting_date"] = str(pr_header["posting_date"]) if pr_header.get("posting_date") else None
		rows = [r for r in pr_rows if r.parent == pr_name]
		out["purchase_receipts"].append({
			"header": pr_header,
			"items_linked_to_po": [dict(r) for r in rows],
		})

	# SE: 通过 Stock Entry Detail.reference_purchase_receipt 关联到 PR
	if not pr_names:
		return out
	se_rows = frappe.get_all(
		"Stock Entry Detail",
		filters={"reference_purchase_receipt": ["in", pr_names]},
		fields=["parent", "idx", "name", "item_code", "qty", "reference_purchase_receipt", "t_warehouse", "s_warehouse"],
	)
	se_names = list({r.parent for r in se_rows})
	for se_name in se_names:
		se_header = frappe.db.get_value(
			"Stock Entry",
			se_name,
			["name", "purpose", "stock_entry_type", "docstatus", "posting_date", "purchase_order", "purchase_receipt_no"],
			as_dict=True,
		)
		if se_header:
			se_header["posting_date"] = str(se_header["posting_date"]) if se_header.get("posting_date") else None
		rows = [r for r in se_rows if r.parent == se_name]
		out["stock_entries"].append({
			"header": se_header,
			"items_with_pr_ref": [dict(r) for r in rows],
		})

	return out


def _get_pr_item_row(purchase_receipt, pr_item_name):
	"""获取采购接收单指定子表行，不存在或 PR 未提交则抛错。"""
	pr = frappe.get_doc("Purchase Receipt", purchase_receipt)
	if pr.docstatus != 1:
		frappe.throw(_("采购接收单 {0} 未提交").format(purchase_receipt))
	for row in pr.items:
		if row.name == pr_item_name:
			return pr, row
	frappe.throw(_("采购接收单 {0} 中未找到行 {1}").format(purchase_receipt, pr_item_name))


def _ensure_po_received_updated(pr, pr_row, pr_item_name):
	"""
	若 PR 行有 purchase_order 但缺少 purchase_order_item，则补全并触发 status_updater，
	使 PO 的 received_qty / per_received 能正确回写（ERPNext 靠 PR 行的 purchase_order_item 关联到 PO 行）。
	"""
	po_name = pr_row.get("purchase_order")
	if not po_name:
		return
	if pr_row.get("purchase_order_item"):
		return
	po_item_name = frappe.db.get_value(
		"Purchase Order Item",
		{"parent": po_name, "item_code": pr_row.get("item_code")},
		"name",
		order_by="idx asc",
	)
	if not po_item_name:
		return
	frappe.db.set_value("Purchase Receipt Item", pr_item_name, "purchase_order_item", po_item_name)
	pr.reload()
	pr.update_prevdoc_status()


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
	创建并提交质检单；若质检通过且良品数量>0 且 to_warehouse 有值，则创建并提交入库（Material Receipt 采购收货）Stock Entry。

	:param purchase_receipt: 采购接收单号（Purchase Receipt name）
	:param pr_item_name: 采购接收单行 name（Purchase Receipt Item 的 name，如 al44lq3fib）
	:param good_qty: 良品数量
	:param defective_qty: 次品数量
	:param order_qty: 订单数量（可选，不传则用 PR 行 qty）
	:param defective_handling: 次品处理意见（可选）：退回 / 报废 / 特采
	:param defective_photos: 次品照片附件，支持多张：传列表 ["url1", "url2"] 或单张 "url"
	:param to_warehouse: 入库目标仓库；有值且质检通过、良品>0 时才会创建 Stock Entry
	:param work_instruction_guide: 作业指导书附件 URL（可选）
	:return: 成功时始终包含 quality_inspection（质检单号）、status（Accepted/Rejected）、stock_entry（入库单号，未创建时为 null）
	"""
	good_qty = flt(good_qty, 6)
	defective_qty = flt(defective_qty, 6)
	order_qty = flt(order_qty, 6) if order_qty is not None else None

	pr, pr_row = _get_pr_item_row(purchase_receipt, pr_item_name)
	sample_size = order_qty if order_qty is not None else flt(pr_row.get("qty"), 6)
	if sample_size <= 0:
		sample_size = 1.0

	# 若 PR 行有 PO 但缺少 purchase_order_item，补全并回写 PO 的 received_qty / per_received
	_ensure_po_received_updated(pr, pr_row, pr_item_name)

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

	# 响应：质检单号、状态、入库单号（未创建时固定为 None，便于前端区分「仅质检」与「质检并入库」）
	out = {"quality_inspection": qi.name, "status": qi.status, "stock_entry": None}

	# 2. 质检通过且良品>0 且指定了目标仓：创建入库 Stock Entry（Material Receipt 采购收货）
	if qi.status != "Accepted" or good_qty <= 0 or not to_warehouse:
		return out

	if not frappe.db.exists("Warehouse", to_warehouse):
		frappe.throw(_("目标仓库 {0} 不存在").format(to_warehouse))

	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	rate = flt(pr_row.get("base_rate") or pr_row.get("rate"), 6)
	cost_center = pr_row.get("cost_center") or frappe.get_cached_value("Company", pr.company, "cost_center")
	# 固定使用 Material Receipt：仅 to_warehouse，不传 from_warehouse，避免被当作转移单或同仓不建单
	se = make_stock_entry(
		item_code=pr_row.item_code,
		qty=good_qty,
		to_warehouse=to_warehouse,
		company=pr.company,
		rate=rate,
		cost_center=cost_center,
		purpose="Material Receipt",
		do_not_save=False,
		do_not_submit=True,
	)
	if se.get("items") and len(se.items) > 0:
		se.items[0].reference_purchase_receipt = purchase_receipt
		# 子表 purchase_order 供 Stock Entry 的 Connections 页显示 PO（dashboard 从 items.purchase_order 取值）
		if pr_row.get("purchase_order") and frappe.get_meta("Stock Entry Detail").has_field("purchase_order"):
			se.items[0].purchase_order = pr_row.get("purchase_order")
	# 建立 PO → SE 关联：从 PR 行带出采购订单，写入 SE 表头，便于追溯与报表
	if pr_row.get("purchase_order"):
		se.purchase_order = pr_row.get("purchase_order")
	se.save(ignore_permissions=True)
	se.submit()
	# 创建了入库单时必须在响应中返回入库单号，供前端成功提示展示
	out["stock_entry"] = se.name
	return out
