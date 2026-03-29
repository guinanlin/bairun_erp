# Copyright (c) 2026, Bairun and contributors
# MES 委外：白名单编排 MR（发料）+ SE 出库 + 回库 SE（草稿）+ PO（可选）。
# 毛坯 PO：单张采购单，半成品料号，单价=po_items 加工费合计÷半成品数量（业务简化视同采购）。
#
# 毛坯入口:
#   bairun_erp.utils.api.mes.blank_outsourcing_submit.submit_blank_outsourcing
#   bench --site site2.local execute bairun_erp.utils.api.mes.blank_outsourcing_submit.submit_blank_outsourcing --kwargs '{"json_data": {...}}'
#
# 半成品→成品入口（默认成品仓、PO 服务项「半成品委外」）:
#   bairun_erp.utils.api.mes.blank_outsourcing_submit.submit_semi_finished_outsourcing
#   bench --site site2.local execute bairun_erp.utils.api.mes.blank_outsourcing_submit.submit_semi_finished_outsourcing --kwargs '{"json_data": {...}}'

from __future__ import unicode_literals

import json
import time

import frappe
from erpnext.manufacturing.doctype.bom.bom import BOMTree
from frappe import _
from frappe.utils import cstr, flt, getdate

from bairun_erp.utils.api.buying.purchase_order_add import _resolve_warehouse, save_purchase_order
from bairun_erp.utils.api.stock.blank_list import FINISHED_WAREHOUSE, SEMI_FINISHED_WAREHOUSE

LOG_DOCTYPE = "MES Blank Outsourcing Log"

ERR_VALIDATION = "VALIDATION_ERROR"
ERR_PERMISSION = "PERMISSION_DENIED"
ERR_MR_FAILED = "MATERIAL_REQUEST_FAILED"
ERR_SE_FAILED = "STOCK_ENTRY_FAILED"
ERR_RECEIPT_FAILED = "RECEIPT_STOCK_ENTRY_FAILED"
ERR_PO_FAILED = "PURCHASE_ORDER_FAILED"
ERR_IDEMPOTENCY = "IDEMPOTENCY_CONFLICT"

PO_DEFAULT_SERVICE_ITEM_NAME = "毛坯委外"
PO_DEFAULT_SERVICE_ITEM_NAME_SEMI = "半成品委外"

CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT = "半成品转成品"


def _parse_body(kwargs):
	jd = kwargs.get("json_data")
	if jd is None:
		jd = kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {}
	return jd if isinstance(jd, dict) else {}


def _summary(params, max_len=400):
	try:
		s = json.dumps(
			{
				"k": (params.get("idempotency_key") or "")[:40],
				"so": params.get("sales_order") or params.get("custom_sales_order_no"),
				"sup": params.get("supplier"),
				"wh": params.get("from_warehouse") or params.get("set_from_warehouse"),
				"n": len(params.get("items") or []),
			},
			ensure_ascii=False,
		)
	except Exception:
		s = "{}"
	return s[:max_len]


def _merge_po_base(base, extra):
	if not extra or not isinstance(extra, dict):
		return base
	out = dict(base)
	for k, v in extra.items():
		if k == "items" and isinstance(v, list) and v:
			out["items"] = v
		elif v is not None and v != "":
			out[k] = v
	return out


def _resolve_po_service_item_code(params_in):
	code = (params_in.get("po_service_item_code") or "").strip()
	if code and frappe.db.exists("Item", code):
		return code
	name = (params_in.get("po_service_item_name") or "").strip() or PO_DEFAULT_SERVICE_ITEM_NAME
	if not name:
		return None
	rows = frappe.get_all(
		"Item",
		filters={"item_name": name},
		fields=["name", "item_code", "is_stock_item"],
		limit=1,
	)
	if not rows:
		return None
	# prefer item_code if present, else name
	it = rows[0]
	return (it.get("item_code") or it.get("name") or "").strip() or None


def _materials_summary_for_po(items):
	lines = []
	for row in items or []:
		if not isinstance(row, dict):
			continue
		ic = (row.get("item_code") or "").strip()
		qty = row.get("qty")
		if not ic:
			continue
		lines.append(f"{ic} x {flt(qty) if qty is not None else ''}".strip())
	if not lines:
		return ""
	# keep short to avoid bloating PO print
	text = "发料明细：" + "；".join(lines[:20])
	if len(lines) > 20:
		text += "；…"
	return text


def _normalize_po_items_to_service(params_in, ctx, po_items):
	"""委外结算 PO：采购行使用服务类 Item（加工费），原发料明细写入 description。"""
	if not po_items or not isinstance(po_items, list):
		return po_items
	service_code = _resolve_po_service_item_code(params_in)
	if not service_code:
		# 若未配置服务 item，则保持原样（兼容旧行为）
		return po_items

	materials_desc = _materials_summary_for_po(ctx.get("items") or params_in.get("items"))
	out = []
	for row in po_items:
		if not isinstance(row, dict):
			continue
		nr = dict(row)
		nr["item_code"] = service_code
		# 若未显式传 description，则补充发料明细，便于对账
		if materials_desc and not (nr.get("description") or "").strip():
			nr["description"] = materials_desc
		out.append(nr)
	return out


def _check_idempotency(key, force_retry):
	"""返回 None 表示可继续；或返回 dict 由调用方直接作为 HTTP 响应。"""
	if not key:
		return None
	rows = frappe.get_all(
		LOG_DOCTYPE,
		filters={"idempotency_key": key},
		fields=[
			"name",
			"status",
			"material_request_name",
			"stock_entry_name",
			"receipt_stock_entry_name",
			"purchase_order_name",
			"purchase_order_semi_finished_name",
		],
		limit=1,
	)
	if not rows:
		return None
	row = rows[0]
	if row.status == "Success":
		return {
			"success": True,
			"replayed": True,
			"operation_id": row.name,
			"material_request_name": row.material_request_name,
			"stock_entry_name": row.stock_entry_name,
			"receipt_stock_entry_name": row.receipt_stock_entry_name or None,
			"purchase_order_name": row.purchase_order_name or None,
			"purchase_order_semi_finished_name": row.get("purchase_order_semi_finished_name")
			or None,
		}
	if force_retry:
		for r in frappe.get_all(LOG_DOCTYPE, filters={"idempotency_key": key}, pluck="name"):
			frappe.delete_doc(LOG_DOCTYPE, r, force=True, ignore_permissions=True)
		frappe.db.commit()
		return None
	return {
		"success": False,
		"error_code": ERR_IDEMPOTENCY,
		"message": _(
			"该幂等键已存在且上次未成功（状态：{0}）。请传 force_retry=true 后重试，或更换 idempotency_key。"
		).format(row.status),
		"material_request_name": row.material_request_name,
		"stock_entry_name": row.stock_entry_name,
		"receipt_stock_entry_name": row.receipt_stock_entry_name or None,
		"purchase_order_name": row.purchase_order_name or None,
		"purchase_order_semi_finished_name": row.get("purchase_order_semi_finished_name") or None,
		"operation_id": row.name,
	}


def _finalize_log(
	log_name,
	status,
	*,
	mr=None,
	se=None,
	receipt_se=None,
	po=None,
	po_semi=None,
	err_code=None,
	err_msg=None,
	t0=None,
	summary=None,
):
	if not log_name or not frappe.db.exists(LOG_DOCTYPE, log_name):
		return
	duration_ms = None
	if t0 is not None:
		duration_ms = int((time.time() - t0) * 1000)
	frappe.db.set_value(
		LOG_DOCTYPE,
		log_name,
		{
			"status": status,
			"material_request_name": mr or "",
			"stock_entry_name": se or "",
			"receipt_stock_entry_name": receipt_se or "",
			"purchase_order_name": po or "",
			"purchase_order_semi_finished_name": po_semi or "",
			"error_code": err_code or "",
			"error_message": (err_msg or "")[:2000] if err_msg else "",
			"duration_ms": duration_ms or 0,
			"request_summary": summary or "",
		},
		update_modified=True,
	)
	frappe.db.commit()


def _create_processing_log(idempotency_key, summary):
	doc = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"idempotency_key": idempotency_key,
			"status": "Processing",
			"request_summary": summary or "",
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _validate_params(p, default_mr_target_warehouse=None):
	items = p.get("items") or []
	if not items or not isinstance(items, list):
		return None, {ERR_VALIDATION: _("items 须为非空数组")}
	for i, row in enumerate(items):
		if not isinstance(row, dict):
			return None, {ERR_VALIDATION: _("items[{0}] 须为对象").format(i)}
		if not row.get("item_code"):
			return None, {ERR_VALIDATION: _("items[{0}].item_code 必填").format(i)}
		qty = row.get("qty")
		if qty is None or flt(qty) <= 0:
			return None, {ERR_VALIDATION: _("items[{0}].qty 须大于 0").format(i)}
	company = (p.get("company") or "").strip() or frappe.defaults.get_user_default("Company")
	if not company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company or not frappe.db.exists("Company", company):
		return None, {ERR_VALIDATION: _("company 无效或未配置")}
	raw_wh = (p.get("from_warehouse") or p.get("set_from_warehouse") or "").strip()
	if not raw_wh:
		return None, {ERR_VALIDATION: _("from_warehouse 或 set_from_warehouse 必填")}
	wh = _resolve_warehouse(raw_wh, company)
	if not wh or not frappe.db.exists("Warehouse", wh):
		return None, {ERR_VALIDATION: _("仓库 '{0}' 无法解析为有效仓库").format(raw_wh)}
	skip_po = bool(p.get("skip_purchase_order"))
	supplier = (p.get("supplier") or p.get("outsourcing_supplier") or "").strip()
	if not skip_po and not supplier:
		return None, {ERR_VALIDATION: _("supplier 必填（或设置 skip_purchase_order=true）")}
	if supplier and not frappe.db.exists("Supplier", supplier):
		return None, {ERR_VALIDATION: _("供应商 '{0}' 不存在").format(supplier)}
	po_items = p.get("po_items") or []
	if not skip_po:
		if not po_items or not isinstance(po_items, list):
			return None, {
				ERR_VALIDATION: _(
					"未跳过 PO 时须传 po_items（采购订单行数组），或与顾问确认后使用 skip_purchase_order=true 仅生成 MR+SE"
				)
			}
		for j, prow in enumerate(po_items):
			if not isinstance(prow, dict) or not prow.get("item_code"):
				return None, {ERR_VALIDATION: _("po_items[{0}].item_code 必填").format(j)}
			if prow.get("qty") is None or flt(prow.get("qty")) < 0:
				return None, {ERR_VALIDATION: _("po_items[{0}].qty 无效").format(j)}
	sales_order = (p.get("sales_order") or p.get("custom_sales_order_no") or "").strip()
	if not sales_order:
		return None, {ERR_VALIDATION: _("sales_order 或 custom_sales_order_no 必填（回库步骤需按 SO/BOM 推导）")}
	if sales_order and not frappe.db.exists("Sales Order", sales_order):
		return None, {ERR_VALIDATION: _("销售订单 '{0}' 不存在").format(sales_order)}
	if default_mr_target_warehouse is None:
		default_mr_target_warehouse = SEMI_FINISHED_WAREHOUSE
	tw = (p.get("target_warehouse") or p.get("to_warehouse") or "").strip()
	if not tw:
		if frappe.db.exists("Warehouse", default_mr_target_warehouse):
			wc = frappe.db.get_value("Warehouse", default_mr_target_warehouse, "company")
			if wc == company:
				tw = default_mr_target_warehouse
	if not tw:
		return None, {
			ERR_VALIDATION: _(
				"请传 target_warehouse（MR 目标仓，须与发料仓不同）；或未配置时确保存在与公司一致的标准仓「{0}」"
			).format(default_mr_target_warehouse)
		}
	tw_resolved = _resolve_warehouse(tw, company)
	if not tw_resolved or tw_resolved == wh:
		return None, {
			ERR_VALIDATION: _("target_warehouse 无效或与发料仓相同，请指定另一有效仓库"),
		}
	return (
		{
			"company": company,
			"warehouse": wh,
			"supplier": supplier,
			"skip_po": skip_po,
			"items": items,
			"po_items": po_items,
			"sales_order": sales_order,
			"custom_business_type": (p.get("custom_business_type") or "").strip() or None,
			"transaction_date": p.get("transaction_date") or getdate(),
			"mr_naming_series": p.get("mr_naming_series") or "MAT-MR-.YYYY.-",
			"purchase_order_extra": (p.get("purchase_order") if isinstance(p.get("purchase_order"), dict) else {}),
			"target_warehouse": tw_resolved,
		},
		None,
	)


def _mr_item_row(company, warehouse, row, schedule_default, target_wh):
	item_code = row["item_code"]
	stock_uom = row.get("uom") or row.get("stock_uom") or frappe.db.get_value("Item", item_code, "stock_uom")
	sd = row.get("schedule_date") or schedule_default
	src = _resolve_warehouse((row.get("from_warehouse") or warehouse), company) or warehouse
	line = {
		"doctype": "Material Request Item",
		"item_code": item_code,
		"qty": flt(row["qty"]),
		"schedule_date": sd,
		"from_warehouse": src,
	}
	tw = (row.get("to_warehouse") or row.get("target_warehouse") or target_wh or "").strip()
	if tw:
		line["warehouse"] = _resolve_warehouse(tw, company) or tw
	elif target_wh:
		line["warehouse"] = target_wh
	if stock_uom:
		line["uom"] = stock_uom
	if row.get("description"):
		line["description"] = row["description"]
	return line


def _sanitize_mr_items_material_issue(mr_doc):
	"""BuyingController 校验：from_warehouse 与 warehouse 不能相同；若 Item 默认把二者填成同一发料仓则清空 target。"""
	for d in mr_doc.get("items") or []:
		if d.from_warehouse and d.warehouse and d.from_warehouse == d.warehouse:
			d.warehouse = None


def _create_material_request(ctx, params):
	td = ctx["transaction_date"]
	schedule_default = ctx.get("schedule_date") or td
	doc_dict = {
		"doctype": "Material Request",
		"naming_series": ctx["mr_naming_series"],
		"material_request_type": "Material Issue",
		"company": ctx["company"],
		"transaction_date": td,
		"schedule_date": schedule_default,
		"set_from_warehouse": ctx["warehouse"],
	}
	meta = frappe.get_meta("Material Request")
	if ctx.get("sales_order") and meta.has_field("custom_sales_order_no"):
		doc_dict["custom_sales_order_no"] = ctx["sales_order"]
	if ctx.get("custom_business_type") and meta.has_field("custom_business_type"):
		doc_dict["custom_business_type"] = ctx["custom_business_type"]
	mr = frappe.get_doc(doc_dict)
	target_wh = (ctx.get("target_warehouse") or "").strip()
	for row in params["items"]:
		mr.append(
			"items",
			_mr_item_row(ctx["company"], ctx["warehouse"], row, schedule_default, target_wh),
		)
	_sanitize_mr_items_material_issue(mr)
	mr.insert()
	mr.submit()
	return mr


def _apply_stock_entry_outsourcing_fields(se, ctx):
	"""Stock Entry 定制：销售订单 custom_customer_order、委外供应商 custom_outsourcing_supplier（见 bairun_erp custom/stock_entry.json）。"""
	meta = frappe.get_meta("Stock Entry")
	so = (ctx.get("sales_order") or "").strip()
	if so and meta.has_field("custom_customer_order"):
		if frappe.db.exists("Sales Order", so):
			se.custom_customer_order = so
	supp = (ctx.get("supplier") or "").strip()
	if supp and meta.has_field("custom_outsourcing_supplier"):
		if frappe.db.exists("Supplier", supp):
			se.custom_outsourcing_supplier = supp


def _create_stock_entry(ctx, mr_doc):
	td = ctx["transaction_date"]
	se = frappe.new_doc("Stock Entry")
	se.company = ctx["company"]
	se.posting_date = td
	se.purpose = "Material Issue"
	se.from_warehouse = ctx["warehouse"]
	se.set_stock_entry_type()
	_apply_stock_entry_outsourcing_fields(se, ctx)
	for mr_item in mr_doc.items:
		se.append(
			"items",
			{
				"item_code": mr_item.item_code,
				"qty": flt(mr_item.qty),
				"uom": mr_item.uom,
				"stock_uom": mr_item.stock_uom or mr_item.uom,
				"conversion_factor": flt(mr_item.conversion_factor) or 1.0,
				"s_warehouse": ctx["warehouse"],
				"material_request": mr_doc.name,
				"material_request_item": mr_item.name,
			},
		)
	se.set_missing_values()
	se.insert()
	se.submit()
	return se


def _build_po_order_data(ctx, params, po_items):
	so = ctx.get("sales_order") or ""
	tw = (ctx.get("target_warehouse") or "").strip()
	items_out = []
	for row in po_items or []:
		if isinstance(row, dict):
			nr = dict(row)
			if tw and not (nr.get("warehouse") or "").strip():
				nr["warehouse"] = tw
			items_out.append(nr)
		else:
			items_out.append(row)
	base = {
		"supplier": ctx["supplier"],
		"company": ctx["company"],
		"transaction_date": cstr(ctx["transaction_date"]),
		"schedule_date": cstr(ctx.get("schedule_date") or ctx["transaction_date"]),
		"items": items_out,
	}
	if tw:
		base["set_warehouse"] = tw
	if so:
		base["order_confirmation_no"] = so
		base["customer_order"] = so
	return _merge_po_base(base, params.get("purchase_order_extra") or {})


def _parse_receipt_semi_line_for_po(ctx, receipt_doc):
	"""从回库草稿 SE 取半成品行：物料、数量、入库仓（与 _run_receipt_step 一致）。"""
	rows = list(getattr(receipt_doc, "items", None) or [])
	if not rows:
		raise ValueError(_("回库草稿无明细，无法生成采购单"))
	first = rows[0]
	item_code = (getattr(first, "item_code", None) or "").strip()
	if not item_code:
		raise ValueError(_("回库草稿行缺少物料编码"))
	qty = flt(getattr(first, "qty", None))
	if qty <= 0:
		raise ValueError(_("回库数量无效，无法生成采购单"))
	tw = (ctx.get("target_warehouse") or "").strip()
	t_wh = (getattr(first, "t_warehouse", None) or "").strip()
	line_wh = tw or t_wh
	return {"item_code": item_code, "qty": qty, "warehouse": line_wh}


def _po_items_processing_total(po_items):
	"""汇总 po_items 中的加工费金额：优先 amount，否则 rate * qty。"""
	total = 0.0
	for row in po_items or []:
		if not isinstance(row, dict):
			continue
		amt = row.get("amount")
		if amt is not None and flt(amt) != 0:
			total += flt(amt)
			continue
		total += flt(row.get("rate")) * flt(row.get("qty"))
	return total


def _build_blank_single_subcontract_po_order_data(ctx, receipt_doc, params_in):
	"""
	毛坯委外单张 PO：行物料为推导的半成品，数量=应回库数量；
	单价 = po_items 加工费合计 / 半成品数量（视同采购半成品，非标准核算）。
	"""
	semi = _parse_receipt_semi_line_for_po(ctx, receipt_doc)
	semi_qty = semi["qty"]
	po_items = ctx.get("po_items") or []
	proc_total = _po_items_processing_total(po_items)
	if proc_total <= 0:
		raise ValueError(
			_("po_items 加工费金额须大于 0（各行列传 amount 或 rate×qty，合计为委外加工费总额）")
		)
	unit_rate = proc_total / semi_qty
	materials_desc = _materials_summary_for_po(ctx.get("items") or params_in.get("items"))
	default_desc = _(
		"毛坯由己方提供；本行单价为委外加工费分摊，视同采购半成品（非标准核算）。"
	)
	custom_desc = (params_in.get("semi_finished_po_item_description") or "").strip()
	desc_parts = [custom_desc or default_desc]
	if materials_desc:
		desc_parts.append(materials_desc)
	description = "；".join(desc_parts)
	item_row = {
		"item_code": semi["item_code"],
		"qty": semi_qty,
		"rate": unit_rate,
		"warehouse": semi["warehouse"],
		"description": description,
	}
	first = po_items[0] if po_items else {}
	for copy_key in ("sales_order", "sales_order_item", "schedule_date", "uom", "stock_uom"):
		val = first.get(copy_key)
		if val is not None and val != "":
			item_row.setdefault(copy_key, val)
	tw = (ctx.get("target_warehouse") or "").strip()
	base = {
		"supplier": ctx["supplier"],
		"company": ctx["company"],
		"transaction_date": cstr(ctx["transaction_date"]),
		"schedule_date": cstr(ctx.get("schedule_date") or ctx["transaction_date"]),
		"items": [item_row],
	}
	if tw:
		base["set_warehouse"] = tw
	so = (ctx.get("sales_order") or "").strip()
	if so:
		base["order_confirmation_no"] = so
		base["customer_order"] = so
	extra = dict(ctx.get("purchase_order_extra") or {})
	extra.pop("items", None)
	if extra:
		return _merge_po_base(base, extra)
	return base


def _pick_bom_name_for_item(item_code, bom_no):
	"""销售行 / Item 解析启用 BOM 编号：行 bom_no → Item.default_bom → 启用 BOM。"""
	if bom_no and frappe.db.exists("BOM", bom_no):
		return bom_no
	default_bom = frappe.db.get_value("Item", item_code, "default_bom")
	if default_bom and frappe.db.exists("BOM", default_bom):
		return default_bom
	row = frappe.get_all(
		"BOM",
		filters={"item": item_code, "is_active": 1, "docstatus": 1},
		fields=["name"],
		order_by="is_default desc, modified desc",
		limit=1,
	)
	return row[0]["name"] if row else None


def _walk_bom_tree_nodes(node):
	yield node
	for ch in node.child_items or []:
		for n in _walk_bom_tree_nodes(ch):
			yield n


def _find_bom_tree_node(root, item_code):
	for n in _walk_bom_tree_nodes(root):
		if n.item_code == item_code:
			return n
	return None


def _subtree_contains_item_code(bom_node, item_code):
	if not bom_node.is_bom:
		return bom_node.item_code == item_code
	for ch in bom_node.child_items or []:
		if not ch.is_bom and ch.item_code == item_code:
			return True
		if ch.is_bom and _subtree_contains_item_code(ch, item_code):
			return True
	return False


def _so_bom_supports_blank_under_parent(tree_root, parent_item, blank_item):
	node = _find_bom_tree_node(tree_root, parent_item)
	if not node or not node.is_bom:
		return False
	return _subtree_contains_item_code(node, blank_item)


def _direct_parent_assemblies_for_blank(blank_item_code, company):
	values = [blank_item_code]
	q = """
		SELECT DISTINCT b.item AS parent_item
		FROM `tabBOM Item` bi
		INNER JOIN `tabBOM` b
			ON b.name = bi.parent AND b.docstatus = 1 AND b.is_active = 1
		WHERE bi.item_code = %s
	"""
	if company:
		q += " AND (IFNULL(b.company,'') = '' OR b.company = %s)"
		values.append(company)
	rows = frappe.db.sql(q, tuple(values), as_dict=True)
	return [r.parent_item for r in rows if r.parent_item]


def _filter_parents_by_sales_order(blank_item, parent_candidates, sales_order):
	so_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": sales_order},
		fields=["item_code", "bom_no"],
		order_by="idx asc",
	)
	matched = []
	for p in parent_candidates:
		for sr in so_items:
			ic = (sr.get("item_code") or "").strip()
			if not ic:
				continue
			bom_name = _pick_bom_name_for_item(ic, sr.get("bom_no"))
			if not bom_name:
				continue
			try:
				tree = BOMTree(bom_name)
			except Exception:
				continue
			if _so_bom_supports_blank_under_parent(tree, p, blank_item):
				matched.append(p)
				break
	seen = set()
	out = []
	for x in matched:
		if x not in seen:
			seen.add(x)
			out.append(x)
	return out


def _resolve_receipt_item_from_issued_blanks_and_so(ctx):
	"""
	回库物料 = 已发毛坯在 BOM 中的直接父项（通常为电镀件等半成品），不得等于毛坯本身。
	销售订单仅用于在 BOM 树中校验「父项—毛坯」路径同属该订单产品结构。
	"""
	so = (ctx.get("sales_order") or "").strip()
	if not so:
		return None, _("sales_order 必填：回库步骤需结合订单 BOM 校验半成品")

	blanks = []
	for row in ctx.get("items") or []:
		if isinstance(row, dict) and (row.get("item_code") or "").strip():
			blanks.append((row.get("item_code") or "").strip())
	if not blanks:
		return None, _("items 中无有效物料编码，无法推导回库半成品")
	blanks = list(dict.fromkeys(blanks))

	company = (ctx.get("company") or "").strip() or None
	resolved = None
	for blank in blanks:
		parents = _direct_parent_assemblies_for_blank(blank, company)
		if not parents:
			return None, _(
				"毛坯 {0} 未在任何已提交、启用的 BOM 中作为子件出现，无法推导半成品"
			).format(blank)
		good = _filter_parents_by_sales_order(blank, parents, so)
		if not good:
			return None, _(
				"销售订单 {0} 的 BOM 无法关联毛坯 {1} 与其半成品父项，请检查多级 BOM 与销售行"
			).format(so, blank)
		if len(good) > 1:
			return None, _("毛坯 {0} 在销售订单下对应多个半成品候选：{1}，请拆单或澄清 BOM").format(
				blank, ",".join(good)
			)
		cand = good[0]
		if cand == blank:
			return None, _("推导出的回库物料与毛坯相同（{0}），请检查 BOM 层级").format(blank)
		if resolved is None:
			resolved = cand
		elif resolved != cand:
			return None, _("多行发料推导不同半成品：{0} 与 {1}，请拆分委外").format(resolved, cand)

	return resolved, None


def _exploded_qty_per_unit_root(bom_name, target_item_code):
	try:
		tree = BOMTree(bom_name)
	except Exception:
		return 0.0
	for node in _walk_bom_tree_nodes(tree):
		if node.item_code == target_item_code:
			return flt(node.exploded_qty)
	return 0.0


def _compute_receipt_qty_from_sales_order_bom(ctx, receipt_item_code):
	"""
	按销售订单 + 多级 BOM 计算应回半成品数量（不受已发毛坯数量限制）：
	SO 行即为半成品则直接累计；否则用该行 BOM 展开取半成品相对根物料的 exploded 倍数 × 行数量。
	"""
	so = (ctx.get("sales_order") or "").strip()
	if not so:
		return 0, _("sales_order 必填：无法计算应回数量")

	so_items = frappe.get_all(
		"Sales Order Item",
		filters={"parent": so},
		fields=["item_code", "stock_qty", "qty", "bom_no"],
		order_by="idx asc",
	)
	if not so_items:
		return 0, _("销售订单 {0} 无明细，无法计算应回数量").format(so)

	qty_total = 0.0
	for r in so_items:
		R = (r.get("item_code") or "").strip()
		if not R:
			continue
		row_qty = flt(r.get("stock_qty") or r.get("qty"))
		if R == receipt_item_code:
			qty_total += row_qty
			continue
		bom_name = _pick_bom_name_for_item(R, r.get("bom_no"))
		if not bom_name:
			continue
		f = _exploded_qty_per_unit_root(bom_name, receipt_item_code)
		if f:
			qty_total += row_qty * f

	if qty_total <= 0:
		return 0, _("销售订单 {0} 无法得到半成品 {1} 的应回库数量（请确认 BOM 含该节点）").format(
			so, receipt_item_code
		)
	return qty_total, None


def _build_receipt_stock_entry_doc(ctx, receipt_item_code, receipt_qty):
	target_wh = (ctx.get("target_warehouse") or "").strip() or SEMI_FINISHED_WAREHOUSE
	stock_uom = frappe.db.get_value("Item", receipt_item_code, "stock_uom")
	se = frappe.new_doc("Stock Entry")
	se.company = ctx["company"]
	se.posting_date = ctx["transaction_date"]
	se.purpose = "Material Receipt"
	se.to_warehouse = target_wh
	se.set_stock_entry_type()
	_apply_stock_entry_outsourcing_fields(se, ctx)
	se.append(
		"items",
		{
			"item_code": receipt_item_code,
			"qty": flt(receipt_qty),
			"uom": stock_uom,
			"stock_uom": stock_uom,
			"conversion_factor": 1.0,
			"t_warehouse": target_wh,
		},
	)
	se.set_missing_values()
	return se


def _run_receipt_step(ctx, mr_doc, se_issue_doc):
	# mr_doc / se_issue_doc 作为后续复杂校验的上下文预留
	_ = mr_doc
	_ = se_issue_doc
	try:
		receipt_item_code, item_err = _resolve_receipt_item_from_issued_blanks_and_so(ctx)
		if item_err:
			return None, cstr(item_err)
		receipt_qty, qty_err = _compute_receipt_qty_from_sales_order_bom(ctx, receipt_item_code)
		if qty_err:
			return None, cstr(qty_err)
		doc = _build_receipt_stock_entry_doc(ctx, receipt_item_code, receipt_qty)
		doc.insert()
		frappe.db.commit()
		return doc, None
	except Exception as e:
		frappe.db.rollback()
		return None, cstr(e)


def _make_fail_response(
	code,
	msg,
	operation_id=None,
	mr=None,
	se=None,
	receipt_se=None,
	po=None,
	po_semi=None,
):
	return {
		"success": False,
		"error_code": code,
		"message": cstr(msg),
		"material_request_name": mr,
		"stock_entry_name": se,
		"receipt_stock_entry_name": receipt_se,
		"purchase_order_name": po,
		"purchase_order_semi_finished_name": po_semi,
		"operation_id": operation_id,
	}


def _make_success_response(operation_id, mr, se, receipt_se, po, po_semi=None):
	return {
		"success": True,
		"replayed": False,
		"operation_id": operation_id,
		"material_request_name": mr,
		"stock_entry_name": se,
		"receipt_stock_entry_name": receipt_se,
		"purchase_order_name": po,
		"purchase_order_semi_finished_name": po_semi,
	}


def _validate_and_build_ctx(params_in, default_mr_target_warehouse=None):
	if default_mr_target_warehouse is None:
		default_mr_target_warehouse = SEMI_FINISHED_WAREHOUSE
	ctx, verr = _validate_params(params_in, default_mr_target_warehouse=default_mr_target_warehouse)
	if verr:
		return None, _make_fail_response(
			list(verr.keys())[0],
			list(verr.values())[0],
			operation_id=None,
		)
	ctx["schedule_date"] = params_in.get("schedule_date") or ctx.get("transaction_date")
	ctx["transaction_date"] = getdate(ctx["transaction_date"])
	return ctx, None


def _create_operation_log(idem, summary):
	if not idem:
		return None
	try:
		return _create_processing_log(idem, summary)
	except frappe.UniqueValidationError:
		frappe.db.rollback()
		return _make_fail_response(
			ERR_IDEMPOTENCY,
			_("幂等键正在处理或已存在，请稍后重试或更换 idempotency_key"),
			operation_id=None,
		)


def _finalize_fail_and_response(
	log_name, summary, t0, code, msg, mr=None, se=None, receipt_se=None, po=None, po_semi=None
):
	if log_name:
		st = "Partial" if (mr or se or receipt_se or po or po_semi) else "Failed"
		_finalize_log(
			log_name,
			st,
			mr=mr,
			se=se,
			receipt_se=receipt_se,
			po=po,
			po_semi=po_semi,
			err_code=code,
			err_msg=msg,
			t0=t0,
			summary=summary,
		)
	return _make_fail_response(
		code, msg, operation_id=log_name, mr=mr, se=se, receipt_se=receipt_se, po=po, po_semi=po_semi
	)


def _finalize_success_and_response(log_name, summary, t0, mr, se, receipt_se, po, po_semi=None):
	if log_name:
		_finalize_log(
			log_name,
			"Success",
			mr=mr,
			se=se,
			receipt_se=receipt_se,
			po=po,
			po_semi=po_semi,
			t0=t0,
			summary=summary,
		)
	return _make_success_response(log_name, mr, se, receipt_se, po, po_semi)


def _check_submit_permissions(skip_po):
	if not frappe.has_permission("Material Request", "create"):
		return _make_fail_response(ERR_PERMISSION, _("无权限创建 Material Request"))
	if not frappe.has_permission("Stock Entry", "create"):
		return _make_fail_response(ERR_PERMISSION, _("无权限创建 Stock Entry"))
	if not skip_po and not frappe.has_permission("Purchase Order", "create"):
		return _make_fail_response(ERR_PERMISSION, _("无权限创建 Purchase Order"))
	return None


def _run_mr_step(ctx):
	try:
		mr_doc = _create_material_request(ctx, ctx)
		frappe.db.commit()
		return mr_doc, None
	except Exception as e:
		frappe.db.rollback()
		return None, cstr(e)


def _run_se_step(ctx, mr_doc):
	try:
		se_doc = _create_stock_entry(ctx, mr_doc)
		frappe.db.commit()
		return se_doc, None
	except Exception as e:
		frappe.db.rollback()
		return None, cstr(e)


def _run_po_step(params_in, ctx, receipt_doc=None, *, blank_single_processing_po=False):
	"""
	半成品→成品委外：归一到服务物料的一张 PO（po_items）。
	毛坯委外 blank_single_processing_po=True：单张 PO，半成品料号 + 单价=加工费总额/数量。
	返回 (purchase_order_name, purchase_order_semi_finished_name|None, error_message|None)。
	第二张 name 仅兼容旧日志字段，毛坯单 PO 模式下恒为 None。
	"""
	try:
		if blank_single_processing_po:
			if not receipt_doc:
				return None, None, _("缺少回库草稿，无法生成采购单")
			po_data = _build_blank_single_subcontract_po_order_data(ctx, receipt_doc, params_in)
			po_result = save_purchase_order(order_data=po_data)
			if po_result.get("error"):
				return None, None, cstr(po_result["error"])
			po_name = (po_result.get("data") or {}).get("name")
			return po_name, None, None
		normalized_po_items = _normalize_po_items_to_service(params_in, ctx, ctx["po_items"])
		po_data = _build_po_order_data(ctx, ctx, normalized_po_items)
		po_result = save_purchase_order(order_data=po_data)
		if po_result.get("error"):
			return None, None, cstr(po_result["error"])
		po_name = (po_result.get("data") or {}).get("name")
		return po_name, None, None
	except Exception as e:
		frappe.db.rollback()
		return None, None, cstr(e)


def _apply_semi_finished_outsourcing_defaults(params_in):
	"""半成品委外入口：补全业务类型与 PO 服务物料名（可被子集显式传入覆盖）。"""
	if not (params_in.get("custom_business_type") or "").strip():
		params_in["custom_business_type"] = CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT
	if not bool(params_in.get("skip_purchase_order")):
		if not (params_in.get("po_service_item_code") or "").strip() and not (
			params_in.get("po_service_item_name") or ""
		).strip():
			params_in["po_service_item_name"] = PO_DEFAULT_SERVICE_ITEM_NAME_SEMI


def _execute_outsourcing_submit(
	params_in, *, default_mr_target_warehouse=None, blank_single_processing_po=False
):
	"""共用编排：幂等 → 校验 → MR → 发料 SE → 回库草稿 SE → PO（可选）。"""
	if default_mr_target_warehouse is None:
		default_mr_target_warehouse = SEMI_FINISHED_WAREHOUSE
	summary = _summary(params_in)
	idem = (params_in.get("idempotency_key") or "").strip() or None
	force_retry = bool(params_in.get("force_retry"))
	t0 = time.time()

	idem_res = _check_idempotency(idem, force_retry)
	if idem_res is not None:
		return idem_res

	ctx, validation_err = _validate_and_build_ctx(
		params_in, default_mr_target_warehouse=default_mr_target_warehouse
	)
	if validation_err:
		return validation_err

	log_or_err = _create_operation_log(idem, summary)
	if isinstance(log_or_err, dict):
		return log_or_err
	log_name = log_or_err

	permission_err = _check_submit_permissions(ctx["skip_po"])
	if permission_err:
		return _finalize_fail_and_response(
			log_name, summary, t0, permission_err["error_code"], permission_err["message"]
		)

	mr_doc, mr_err = _run_mr_step(ctx)
	if mr_err:
		return _finalize_fail_and_response(log_name, summary, t0, ERR_MR_FAILED, mr_err)
	mr_name = mr_doc.name

	se_doc, se_err = _run_se_step(ctx, mr_doc)
	if se_err:
		return _finalize_fail_and_response(log_name, summary, t0, ERR_SE_FAILED, se_err, mr=mr_name)
	se_name = se_doc.name

	receipt_doc, receipt_err = _run_receipt_step(ctx, mr_doc, se_doc)
	if receipt_err:
		return _finalize_fail_and_response(
			log_name, summary, t0, ERR_RECEIPT_FAILED, receipt_err, mr=mr_name, se=se_name
		)
	receipt_name = receipt_doc.name

	if ctx["skip_po"]:
		return _finalize_success_and_response(log_name, summary, t0, mr_name, se_name, receipt_name, None)

	po_name, po_semi_name, po_err = _run_po_step(
		params_in, ctx, receipt_doc, blank_single_processing_po=blank_single_processing_po
	)
	if po_err:
		return _finalize_fail_and_response(
			log_name,
			summary,
			t0,
			ERR_PO_FAILED,
			po_err,
			mr=mr_name,
			se=se_name,
			receipt_se=receipt_name,
			po=po_name,
			po_semi=po_semi_name,
		)
	return _finalize_success_and_response(
		log_name, summary, t0, mr_name, se_name, receipt_name, po_name, po_semi_name
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def submit_blank_outsourcing(**kwargs):
	"""
	委外编排：创建并提交 MR（Material Issue）→ SE（Material Issue）→ 回库 SE（Material Receipt，仅草稿）→ PO（可选 skip）。

	json_data / body:
	- company, from_warehouse | set_from_warehouse, items[{item_code, qty, schedule_date?, uom?}]
	- supplier / outsourcing_supplier：未 skip PO 时必填；仅 MR+SE 时可选，但建议传入以便 SE 写入委外供应商
	- sales_order | custom_sales_order_no（可选，须为 Sales Order name）
	- custom_business_type（可选，须与 Material Request 自定义选项一致）
	- skip_purchase_order: 为 true 时跳过 PO
	- po_items: 未 skip 时必填。每行须含 item_code（可仍为服务类占位物料）；**加工费**用 amount 或 rate×qty 表达，**多行金额相加**为委外加工费总额。最终仅生成**一张 PO**：物料为 BOM 推导的**半成品**，数量=应回库数量，**单价=加工费总额÷半成品数量**。
	- target_warehouse / to_warehouse: MR 目标仓（须与发料仓不同）；未传时若存在「半成品 - B」且属本公司则作默认
	- purchase_order: 可选 dict，合并进 PO 头（如 naming_series、taxes、title）；**勿传 items**（行由系统生成）
	- semi_finished_po_item_description: 可选，追加到 PO 行说明前缀
	- idempotency_key: 可选，成功后可重放；失败需 force_retry=true
	- force_retry: 可选

	成功时：仅 purchase_order_name；purchase_order_semi_finished_name 恒为 null（字段保留兼容旧日志）。
	"""
	params_in = _parse_body(kwargs)
	return _execute_outsourcing_submit(
		params_in,
		default_mr_target_warehouse=SEMI_FINISHED_WAREHOUSE,
		blank_single_processing_po=True,
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
def submit_semi_finished_outsourcing(**kwargs):
	"""
	半成品→成品委外编排：与 submit_blank_outsourcing 同流程；默认 MR 目标仓「成品 - B」、
	custom_business_type「半成品转成品」、PO 服务行默认按 Item.item_name「半成品委外」解析（可显式覆盖）。

	json_data 字段与 submit_blank_outsourcing 相同；未传 target_warehouse 时尝试默认成品仓（须与公司一致）。
	"""
	params_in = dict(_parse_body(kwargs))
	_apply_semi_finished_outsourcing_defaults(params_in)
	return _execute_outsourcing_submit(params_in, default_mr_target_warehouse=FINISHED_WAREHOUSE)
