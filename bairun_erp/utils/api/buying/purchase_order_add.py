# Copyright (c) 2025, Bairun and contributors
# 采购订单创建接口：通过 API 创建 ERPNext Purchase Order。
#
# 与 frappe.desk.form.save.savedocs 的对应关系：
# - savedocs(doc, action) 接收前端整单 JSON，经 json.loads -> get_doc -> set_local_name 后
#   根据 action 设置 docstatus 并执行 doc.save() 或 doc.submit()，最后通过 send_updated_docs 返回文档。
# - 本模块提供 save_purchase_order(order_data)，用精简的 order_data 构建 PO 文档并保存，
#   便于后端/集成调用，且便于扩展（校验、默认值、税、自定义字段等）。
#
# bench execute 示例:
#   bench --site site1.local execute bairun_erp.utils.api.buying.purchase_order_add.save_purchase_order --kwargs '{"order_data": {...}}'

from __future__ import unicode_literals

import json
import re

import frappe
from frappe import _
from frappe.utils import flt, getdate

# 允许从 order_data 透传到 Purchase Order 主表的字段（除下方 _prepare_po_* 已处理的以外，可在此扩展）
_PO_HEADER_EXTRA_FIELDS = frozenset({
	"naming_series", "apply_tds", "tax_withholding_category", "is_subcontracted",
	"cost_center", "project", "buying_price_list", "ignore_pricing_rule",
	"shipping_address", "billing_address", "language", "tax_category",
	"payment_terms_template", "tc_name", "terms",
	"customer_order",  # 关联的销售订单号（自定义字段，用于列表/Connections 显示）
	"order_confirmation_no", "order_confirmation_date",  # 订单确认号/日期，通常填销售订单号以关联 SO
})

# Purchase Order Item 子表允许的字段（用于从 order_data.items 过滤并写入子表）
_PO_ITEM_FIELDS = frozenset({
	"item_code", "item_name", "description", "qty", "rate", "amount",
	"uom", "stock_uom", "conversion_factor", "schedule_date", "warehouse",
	"price_list_rate", "discount_percentage", "discount_amount",
	"expense_account", "cost_center", "item_group", "image",
	"material_request", "material_request_item", "supplier_quotation", "supplier_quotation_item",
	"weight_per_unit", "total_weight", "item_tax_rate",
	"sales_order", "sales_order_item", "sales_order_packed_item",  # 关联销售订单，用于 ERPNext Connections 显示
})


def _parse_order_data(order_data, kwargs):
	"""解析 order_data：支持 json_data 包装和 FormData 字符串。"""
	if not order_data and kwargs.get("json_data"):
		jd = kwargs["json_data"]
		if isinstance(jd, dict):
			order_data = jd.get("order_data") or jd
		elif isinstance(jd, str):
			try:
				jd = json.loads(jd)
				order_data = jd.get("order_data") if isinstance(jd, dict) else jd
			except json.JSONDecodeError:
				return None
		else:
			order_data = None

	if isinstance(order_data, str):
		try:
			order_data = json.loads(order_data)
		except json.JSONDecodeError:
			return None

	return order_data


def _validate_order_data(order_data):
	"""校验 order_data 必填项。成功返回 (order_data, None)，失败返回 (None, error_dict)。"""
	if not order_data or not isinstance(order_data, dict):
		return None, {"error": _("Invalid input format. Expected dict or JSON string.")}

	if order_data.get("doctype") and order_data.get("doctype") != "Purchase Order":
		return None, {"error": _("Invalid doctype. Must be Purchase Order or omit.")}

	# 供应商
	supplier = order_data.get("supplier")
	if not supplier:
		return None, {"error": _("供应商不能为空")}
	if not frappe.db.exists("Supplier", supplier):
		return None, {"error": _("供应商 '{0}' 不存在").format(supplier)}

	# 公司
	company = order_data.get("company")
	if not company:
		return None, {"error": _("公司不能为空")}
	if not frappe.db.exists("Company", company):
		return None, {"error": _("公司 '{0}' 不存在").format(company)}

	# 明细
	items = order_data.get("items") or []
	if not items:
		return None, {"error": _("至少需要一行采购明细")}

	for i, row in enumerate(items):
		idx = i + 1
		item_code = row.get("item_code")
		if not item_code:
			return None, {"error": _("采购明细第 {0} 行物料编码不能为空").format(idx)}
		if not frappe.db.exists("Item", item_code):
			return None, {"error": _("物料 '{0}' 不存在").format(item_code)}
		qty = row.get("qty")
		if qty is None or flt(qty) < 0:
			return None, {"error": _("采购明细第 {0} 行数量不能为负").format(idx)}
		rate = row.get("rate")
		if rate is not None and isinstance(rate, (int, float)) and flt(rate) < 0:
			return None, {"error": _("采购明细第 {0} 行单价不能为负").format(idx)}

	# 日期格式
	for field in ("transaction_date", "schedule_date"):
		val = order_data.get(field)
		if val:
			if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val).strip()):
				return None, {"error": _("{0} 格式需为 YYYY-MM-DD").format(field)}
			try:
				getdate(val)
			except Exception:
				return None, {"error": _("{0} 格式需为 YYYY-MM-DD").format(field)}

	# 成本中心若传则须存在
	cc = order_data.get("cost_center")
	if cc and not frappe.db.exists("Cost Center", cc):
		return None, {"error": _("成本中心 '{0}' 不存在").format(cc)}

	return order_data, None


def _to_purchase_order_item(row):
	"""将一行 item 转为 Purchase Order Item 子表结构，仅保留允许字段。"""
	out = {"doctype": "Purchase Order Item"}
	for k, v in row.items():
		if k in _PO_ITEM_FIELDS and v is not None and v != "":
			out[k] = v
	return out


def _get_default_warehouse(company):
	"""获取公司下第一个非组仓库。"""
	wh = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name"],
		limit=1,
	)
	return wh[0].name if wh else None


def _resolve_warehouse(warehouse, company):
	"""
	将前端传入的仓库简称解析为 ERPNext 中的完整仓库 name（带公司后缀）。
	例如 "毛坯" -> "毛坯 - B"，避免 set_missing_values -> get_child_warehouses 时
	frappe.db.get_value("Warehouse", "毛坯", ["lft", "rgt"]) 返回 None 导致解包报错。
	"""
	if not warehouse or not company:
		return warehouse
	if frappe.db.exists("Warehouse", warehouse):
		return warehouse
	abbr = frappe.get_cached_value("Company", company, "abbr")
	if abbr:
		full_name = warehouse + " - " + abbr
		if frappe.db.exists("Warehouse", full_name):
			return full_name
	# 按 warehouse_name 或 name 包含匹配
	wh = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		or_filters=[
			{"warehouse_name": warehouse},
			{"name": ["like", "%" + warehouse + "%"]},
		],
		fields=["name"],
		limit=1,
	)
	return wh[0].name if wh else None


def _prepare_po_header(order_data, default_warehouse):
	"""构建采购订单主表字段（便于后续扩展或覆盖）。"""
	company = order_data.get("company")
	currency = frappe.get_cached_value("Company", company, "default_currency") or "CNY"
	supplier_name = frappe.get_cached_value("Supplier", order_data.get("supplier"), "supplier_name") or order_data.get("supplier")
	raw_set_wh = order_data.get("set_warehouse") or default_warehouse or ""
	resolved_set = _resolve_warehouse(raw_set_wh, company) if raw_set_wh else None
	set_warehouse = resolved_set or default_warehouse or ""
	header = {
		"doctype": "Purchase Order",
		"title": order_data.get("title") or supplier_name,
		"supplier": order_data.get("supplier"),
		"company": company,
		"transaction_date": order_data.get("transaction_date") or getdate(),
		"schedule_date": order_data.get("schedule_date") or order_data.get("transaction_date") or getdate(),
		"currency": order_data.get("currency") or currency,
		"conversion_rate": flt(order_data.get("conversion_rate"), 1) or 1,
		"price_list_currency": order_data.get("price_list_currency") or currency,
		"plc_conversion_rate": flt(order_data.get("plc_conversion_rate"), 1) or 1,
		"set_warehouse": set_warehouse,
		"apply_discount_on": order_data.get("apply_discount_on") or "Grand Total",
		"naming_series": order_data.get("naming_series") or "PUR-ORD-.YYYY.-",
	}
	# 扩展字段
	for k in _PO_HEADER_EXTRA_FIELDS:
		if order_data.get(k) is not None:
			header[k] = order_data[k]
	return header


def _prepare_po_items(order_data, default_warehouse):
	"""构建 items 子表（便于后续扩展或覆盖）。"""
	company = order_data.get("company")
	schedule_date = order_data.get("schedule_date") or order_data.get("transaction_date") or getdate()
	raw_set_wh = order_data.get("set_warehouse") or default_warehouse
	resolved_set = _resolve_warehouse(raw_set_wh, company) if raw_set_wh else None
	set_warehouse = resolved_set or default_warehouse or ""

	items = []
	for idx, row in enumerate(order_data.get("items") or []):
		po_item = _to_purchase_order_item(row)
		if not po_item.get("item_code"):
			continue
		if not po_item.get("schedule_date"):
			po_item["schedule_date"] = schedule_date
		raw_wh = po_item.get("warehouse") or set_warehouse
		resolved = _resolve_warehouse(raw_wh, company) if raw_wh else None
		po_item["warehouse"] = resolved or set_warehouse or ""
		po_item["idx"] = idx + 1
		items.append(po_item)
	return items


def _prepare_po_taxes(order_data):
	"""构建 taxes 子表（可选）。"""
	taxes = order_data.get("taxes") or []
	out = []
	for t in taxes:
		if not isinstance(t, dict):
			continue
		row = {
			"doctype": "Purchase Taxes and Charges",
			"charge_type": t.get("charge_type") or "On Net Total",
			"account_head": t.get("account_head") or "",
			"description": t.get("description") or "",
			"rate": flt(t.get("rate"), 0),
			"cost_center": t.get("cost_center") or "",
			"add_deduct_tax": t.get("add_deduct_tax") or "Add",
			"category": t.get("category") or "Total",
		}
		if row["account_head"] or row["description"] or row["rate"]:
			row["idx"] = len(out) + 1
			out.append(row)
	return out


def _build_po_doc_dict(order_data, default_warehouse):
	"""将 order_data 转为可传入 frappe.get_doc 的完整字典（扩展点：可在此或上层替换 header/items/taxes）。"""
	doc_dict = _prepare_po_header(order_data, default_warehouse)
	doc_dict["items"] = _prepare_po_items(order_data, default_warehouse)
	doc_dict["taxes"] = _prepare_po_taxes(order_data)

	# 更新场景：传入 name 且存在
	if order_data.get("name") and frappe.db.exists("Purchase Order", order_data["name"]):
		doc_dict["name"] = order_data["name"]

	return doc_dict


def _before_save_po(doc, order_data):
	"""保存前钩子：子模块或定制可覆盖，用于修改 doc。"""
	pass


def _merge_purchase_order_no_field(existing, new_po):
	"""多单号逗号拼接；已含同号则不变。"""
	existing = (existing or "").strip()
	new_po = (new_po or "").strip()
	if not new_po:
		return existing or ""
	if not existing:
		return new_po
	parts = [p.strip() for p in existing.split(",") if p.strip()]
	if new_po in parts:
		return ",".join(parts)
	parts.append(new_po)
	return ",".join(parts)


def _update_br_so_bom_detail_po_fields(detail_row_name, po_name):
	"""回写 BR SO BOM List Details：采购单号 + 生单状态。"""
	if not detail_row_name or not po_name:
		return
	if not frappe.db.exists("BR SO BOM List Details", detail_row_name):
		return
	existing = frappe.db.get_value("BR SO BOM List Details", detail_row_name, "purchase_order_no")
	merged = _merge_purchase_order_no_field(existing, po_name)
	frappe.db.set_value(
		"BR SO BOM List Details",
		detail_row_name,
		{
			"purchase_order_no": merged,
			"order_status": "已生单",
		},
		update_modified=True,
	)


def _resolved_finished_item_code_for_bom_list(order_data, po_item):
	"""BR SO BOM List 主表命名：{sales_order}-{成品 item_code}。"""
	for key in ("bom_finished_item_code", "finished_item_code", "bomFinishedItemCode", "finishedItemCode"):
		v = order_data.get(key) if isinstance(order_data, dict) else None
		if v is not None and str(v).strip():
			return str(v).strip()
	if not getattr(po_item, "sales_order_item", None):
		return ""
	finished = frappe.db.get_value("Sales Order Item", po_item.sales_order_item, "item_code")
	return (finished or "").strip()


def _resolve_br_so_bom_parent_names(order_data, po_item, so_name):
	"""返回可能命中的 BR SO BOM List 主表名列表。"""
	if not so_name:
		return []
	explicit_finished = _resolved_finished_item_code_for_bom_list(order_data, po_item)
	if explicit_finished:
		return ["{0}-{1}".format(so_name, explicit_finished)]
	# 当未传 sales_order_item 时，按 SO + 子表 item_code / supplier_code 反查 parent
	item_code = (getattr(po_item, "item_code", None) or "").strip()
	if not item_code:
		return []
	filters = {"order_no": so_name}
	details_filters = {"item_code": item_code}
	supplier = (getattr(po_item, "supplier", None) or "").strip()
	if not supplier and getattr(po_item, "parent", None):
		# po_item 没有 supplier 字段，回退用 PO 头 supplier（调用处会传 doc.supplier 再二次过滤）
		supplier = ""
	if supplier:
		details_filters["supplier_code"] = supplier
	parent_names = frappe.get_all(
		"BR SO BOM List Details",
		filters=details_filters,
		fields=["parent"],
		pluck="parent",
	)
	if not parent_names:
		return []
	valid = []
	for parent_name in parent_names:
		if frappe.db.exists("BR SO BOM List", {"name": parent_name, "order_no": so_name}):
			valid.append(parent_name)
	# 去重并保持顺序
	seen = set()
	out = []
	for p in valid:
		if p in seen:
			continue
		seen.add(p)
		out.append(p)
	return out


def _sync_br_so_bom_list_details_from_saved_po(doc, order_data):
	"""
	一键生单成功后：把采购单号、生单状态写回 BR SO BOM List Details，
	以便 get_product_bom_list_new 重拉列表与库内一致。

	匹配优先级（每条 PO 明细一行）：
	1) items[i].br_so_bom_list_detail_name / bom_list_detail_name（子表行 name，最准）
	2) sales_order + 成品编码 + item_code + supplier_code（与明细行供应商一致）
	3) 同上 + bom_code / bomCode（行上可选，用于重复物料行）
	4) 仅 item_code 唯一则更新；否则更新 idx 最小的一行（保守）
	"""
	if not doc or not getattr(doc, "name", None):
		return
	order_data = order_data if isinstance(order_data, dict) else {}
	items_od = order_data.get("items") or []
	po_items = list(doc.items or [])
	for idx, po_item in enumerate(po_items):
		row_meta = items_od[idx] if idx < len(items_od) and isinstance(items_od[idx], dict) else {}
		detail_name = (
			(row_meta.get("br_so_bom_list_detail_name") or "").strip()
			or (row_meta.get("bom_list_detail_name") or "").strip()
			or (row_meta.get("brSoBomListDetailName") or "").strip()
			or (row_meta.get("bomListDetailName") or "").strip()
		)
		if detail_name:
			_update_br_so_bom_detail_po_fields(detail_name, doc.name)
			continue
		so_name = (getattr(po_item, "sales_order", None) or "").strip()
		if not so_name:
			so_name = (
				(order_data.get("order_confirmation_no") or order_data.get("customer_order") or "")
				.strip()
			)
		if not so_name:
			continue
		parent_names = _resolve_br_so_bom_parent_names(order_data, po_item, so_name)
		if not parent_names:
			continue
		item_code = (getattr(po_item, "item_code", None) or "").strip()
		if not item_code:
			continue
		supplier = (getattr(doc, "supplier", None) or "").strip()
		bom_code_hint = (
			(row_meta.get("bom_code") or row_meta.get("bomCode") or "").strip()
		)
		hit = False
		for parent_name in parent_names:
			base_filters = {"parent": parent_name, "item_code": item_code}
		# 供应商一致（明细 supplier_code 常与 Supplier 主档 name 一致）
			if supplier:
				by_supp = frappe.get_all(
					"BR SO BOM List Details",
					filters=dict(base_filters, supplier_code=supplier),
					pluck="name",
					order_by="idx asc",
				)
				if len(by_supp) == 1:
					_update_br_so_bom_detail_po_fields(by_supp[0], doc.name)
					hit = True
					break
				if len(by_supp) > 1 and bom_code_hint:
					by_bom = frappe.get_all(
						"BR SO BOM List Details",
						filters=dict(base_filters, supplier_code=supplier, bom_code=bom_code_hint),
						pluck="name",
						order_by="idx asc",
					)
					for nm in by_bom:
						_update_br_so_bom_detail_po_fields(nm, doc.name)
					if by_bom:
						hit = True
						break
					_update_br_so_bom_detail_po_fields(by_supp[0], doc.name)
					hit = True
					break
				if len(by_supp) > 1:
					_update_br_so_bom_detail_po_fields(by_supp[0], doc.name)
					hit = True
					break
			# 无供应商命中或未传 supplier：bom_code 提示
			if bom_code_hint and not hit:
				by_bom = frappe.get_all(
					"BR SO BOM List Details",
					filters=dict(base_filters, bom_code=bom_code_hint),
					pluck="name",
					order_by="idx asc",
				)
				if len(by_bom) == 1:
					_update_br_so_bom_detail_po_fields(by_bom[0], doc.name)
					hit = True
					break
				if len(by_bom) > 1:
					_update_br_so_bom_detail_po_fields(by_bom[0], doc.name)
					hit = True
					break
			if hit:
				break
			candidates = frappe.get_all(
				"BR SO BOM List Details",
				filters=base_filters,
				pluck="name",
				order_by="idx asc",
			)
			if len(candidates) == 1:
				_update_br_so_bom_detail_po_fields(candidates[0], doc.name)
				break
			elif len(candidates) > 1:
				_update_br_so_bom_detail_po_fields(candidates[0], doc.name)
				break


def _after_save_po(doc, order_data):
	"""保存后钩子：若 PO 行带有 sales_order + sales_order_item，回写 Sales Order Item 的 purchase_order，使销售订单的 Connections 能显示本采购单。"""
	for item in (doc.items or []):
		if not item.sales_order_item or not item.sales_order:
			continue
		if not frappe.db.exists("Sales Order Item", item.sales_order_item):
			continue
		frappe.db.set_value(
			"Sales Order Item",
			item.sales_order_item,
			"purchase_order",
			doc.name,
			update_modified=False,
		)
	_sync_br_so_bom_list_details_from_saved_po(doc, order_data or {})


def _do_insert_save_po(order_data):
	"""对已校验的 order_data 执行组 doc、insert、save、submit，不 commit。用于单笔与批量共用，保证批量时同一事务。保存后自动提交为已审核状态。"""
	company = order_data.get("company")
	default_warehouse = _get_default_warehouse(company)
	doc_dict = _build_po_doc_dict(order_data, default_warehouse)
	is_update = bool(
		order_data.get("name") and frappe.db.exists("Purchase Order", order_data["name"])
	)
	if is_update:
		po = frappe.get_doc("Purchase Order", order_data["name"])
		doc_dict.pop("name", None)
		po.update(doc_dict)
		_before_save_po(po, order_data)
		po.flags.ignore_validate_update_after_submit = True
		po.save(ignore_permissions=True)
		if po.docstatus == 0:
			po.flags.ignore_permissions = True
			po.submit()
	else:
		doc_dict.pop("name", None)
		po = frappe.get_doc(doc_dict)
		po.set_missing_values()
		_before_save_po(po, order_data)
		po.insert(ignore_permissions=True)
		po.save(ignore_permissions=True)
		po.flags.ignore_permissions = True
		po.submit()
	_after_save_po(po, order_data)
	return po


def _parse_order_data_list(order_data_list, kwargs):
	"""解析批量接口的 order_data_list：支持 json_data 包装及 order_data_list / orders 键。"""
	if not order_data_list and kwargs.get("json_data"):
		jd = kwargs["json_data"]
		if isinstance(jd, dict):
			order_data_list = jd.get("order_data_list") or jd.get("orders")
		elif isinstance(jd, str):
			try:
				jd = json.loads(jd)
				order_data_list = (jd.get("order_data_list") or jd.get("orders")) if isinstance(jd, dict) else None
			except json.JSONDecodeError:
				return None
		else:
			order_data_list = None
	if isinstance(order_data_list, str):
		try:
			order_data_list = json.loads(order_data_list)
		except json.JSONDecodeError:
			return None
	out = order_data_list if isinstance(order_data_list, list) else None
	return out


def _extract_line_item_code(order_data):
	"""提取当前行的主物料编码（优先取第一条 items.item_code）。"""
	if not isinstance(order_data, dict):
		return ""
	items = order_data.get("items") or []
	if isinstance(items, list):
		for row in items:
			if isinstance(row, dict):
				ic = (row.get("item_code") or "").strip()
				if ic:
					return ic
	return ""


@frappe.whitelist(allow_guest=False)
def save_purchase_order(order_data=None, *args, **kwargs):
	"""
	创建或更新采购订单（Purchase Order），保存后自动提交为已审核状态。

	入参:
		order_data: 采购订单数据 dict，或通过 json_data 传入。
		支持:
		  - { "order_data": {...} }
		  - { "json_data": { "order_data": {...} } }
		主表必填: supplier, company
		主表可选: transaction_date, schedule_date, currency, set_warehouse, naming_series, taxes,
		  order_confirmation_no（订单确认号，填销售订单号可在 PO 上显示并关联 SO）,
		  order_confirmation_date（可选）, customer_order（自定义字段，若有）, ...
		子表 items 每行必填: item_code；建议: qty, rate, schedule_date, warehouse, cost_center, expense_account；
		若需在采购单与销售订单双向关联（PO 上显示 Order Confirmation No + 两侧 Connections）：
		  主表传 order_confirmation_no = 销售订单 name；
		  items 每行传 sales_order = 销售订单 name，sales_order_item = 对应 Sales Order Item 的 name（必填以便回写 SO 行）。

	返回:
		成功: { "data": { "success": True, "name": "PUR-ORD-YYYY-xxxxx", "message": "...", "doc": {...} } }
		失败: { "error": "错误信息" }
	"""
	try:
		if not order_data and args and isinstance(args[0], (str, dict)):
			order_data = args[0]
		elif not order_data and kwargs:
			order_data = _parse_order_data(None, kwargs)
		else:
			order_data = _parse_order_data(order_data, kwargs)

		order_data, err = _validate_order_data(order_data)
		if err:
			return err

		po = _do_insert_save_po(order_data)
		frappe.db.commit()

		doc_as_dict = po.as_dict()
		return {
			"data": {
				"success": True,
				"name": po.name,
				"message": _("采购订单已保存并提交"),
				"doc": doc_as_dict,
			}
		}
	except frappe.ValidationError as e:
		frappe.db.rollback()
		return {"error": str(e)}
	except Exception as e:
		frappe.db.rollback()
		return {"error": str(e)}


@frappe.whitelist(allow_guest=False)
def save_purchase_orders(order_data_list=None, *args, **kwargs):
	"""
	批量创建或更新采购订单，保存后自动提交为已审核状态；保证事务完整性：要么全部成功，要么全部回滚。

	入参:
		order_data_list: 采购订单数据列表，每项与 save_purchase_order 的 order_data 结构相同。
		支持:
		  - { "order_data_list": [ {...}, {...} ] }
		  - { "json_data": { "order_data_list": [ {...}, {...} ] } }
		  - { "json_data": { "orders": [ {...}, {...} ] } } 也可识别

	返回:
		成功: { "data": { "success": True, "count": N, "names": [...], "docs": [...], "message": "..." } }
		失败: { "error": "错误信息", "index": 第几条(0-based)失败，校验失败时带 index }
	"""
	try:
		if not order_data_list and args and isinstance(args[0], (str, list)):
			order_data_list = json.loads(args[0]) if isinstance(args[0], str) else args[0]
		elif not order_data_list and kwargs:
			order_data_list = _parse_order_data_list(None, kwargs)
		else:
			order_data_list = _parse_order_data_list(order_data_list, kwargs)

		# 兼容：请求体整体作为第一参数传入（如部分代理/客户端把 body 放在 data 或 args[0]）
		if (not order_data_list or not isinstance(order_data_list, list)) and args and isinstance(args[0], dict):
			body = args[0]
			order_data_list = body.get("order_data_list") or body.get("orders")
			if not order_data_list and isinstance(body.get("json_data"), dict):
				order_data_list = body["json_data"].get("order_data_list") or body["json_data"].get("orders")
		if isinstance(order_data_list, str):
			try:
				order_data_list = json.loads(order_data_list)
			except json.JSONDecodeError:
				order_data_list = None

		if not order_data_list or not isinstance(order_data_list, list):
			return {"error": _("请提供 order_data_list 数组，且至少包含一张采购订单")}

		# 先整体校验，不写库
		validated = []
		line_results = []
		for i, od in enumerate(order_data_list):
			line_no = i + 1
			line_item_code = _extract_line_item_code(od)
			parsed = _parse_order_data(od, {})
			to_validate = parsed if parsed is not None else od
			result = _validate_order_data(to_validate)
			if result is None or not isinstance(result, (tuple, list)) or len(result) != 2:
				line_results.append({
					"line_no": line_no,
					"item_code": line_item_code,
					"success": False,
					"purchase_order_no": None,
					"order_status": "未生单",
					"message": _("校验返回异常，请检查第 {0} 条数据格式").format(i),
				})
				return {
					"success": False,
					"error": _("校验返回异常，请检查第 {0} 条数据格式").format(i),
					"index": i,
					"data": {
						"count": 0,
						"line_results": line_results,
					},
				}
			od, err = result
			if err:
				line_results.append({
					"line_no": line_no,
					"item_code": line_item_code or _extract_line_item_code(od),
					"success": False,
					"purchase_order_no": None,
					"order_status": "未生单",
					"message": err.get("error", str(err)),
				})
				return {
					"success": False,
					"error": err.get("error", str(err)),
					"index": i,
					"data": {
						"count": 0,
						"line_results": line_results,
					},
				}
			validated.append(od)
			line_results.append({
				"line_no": line_no,
				"item_code": _extract_line_item_code(od) or line_item_code,
				"success": False,
				"purchase_order_no": None,
				"order_status": "未生单",
				"message": "",
			})

		# 同一事务内依次 insert/save，不在此处 commit
		docs = []
		for i, order_data in enumerate(validated):
			po = _do_insert_save_po(order_data)
			docs.append(po.as_dict())
			line_results[i]["success"] = True
			line_results[i]["purchase_order_no"] = po.name
			line_results[i]["order_status"] = "已生单"
			line_results[i]["message"] = ""

		frappe.db.commit()

		names = [d["name"] for d in docs]
		return {
			"data": {
				"success": True,
				"count": len(docs),
				"names": names,
				"docs": docs,
				"line_results": line_results,
				"message": _("已批量保存并提交 {0} 张采购订单").format(len(docs)),
			}
		}
	except frappe.ValidationError as e:
		frappe.db.rollback()
		return {
			"success": False,
			"error": str(e),
		}
	except Exception as e:
		frappe.db.rollback()
		return {
			"success": False,
			"error": str(e),
		}


def test_insert_one_po():
	"""
	测试插入一张采购订单（使用当前站点第一个供应商、公司、物料）。
	运行: bench --site <site> execute bairun_erp.utils.api.buying.purchase_order_add.test_insert_one_po
	"""
	suppliers = frappe.get_all("Supplier", fields=["name"], limit=1)
	companies = frappe.get_all("Company", fields=["name"], limit=1)
	items = frappe.get_all("Item", filters={"is_stock_item": 1}, fields=["name"], limit=1)
	if not suppliers or not companies or not items:
		return {"error": "站点中需要至少一个 Supplier、一个 Company、一个库存物料才能运行测试"}
	supplier = suppliers[0].name
	company = companies[0].name
	item_code = items[0].name
	item_doc = frappe.get_cached_doc("Item", item_code)
	order_data = {
		"supplier": supplier,
		"company": company,
		"transaction_date": getdate(),
		"schedule_date": getdate(),
		"items": [
			{
				"item_code": item_code,
				"item_name": item_doc.item_name or item_code,
				"qty": 1,
				"rate": 10,
				"schedule_date": getdate(),
			}
		],
	}
	result = save_purchase_order(order_data=order_data)
	if result.get("error"):
		return result
	return result


def test_insert_batch_po():
	"""
	测试批量保存采购订单（使用当前站点前 2 个供应商、1 个公司、2 个物料生成 2 张 PO）。
	运行: bench --site <site> execute bairun_erp.utils.api.buying.purchase_order_add.test_insert_batch_po
	"""
	suppliers = frappe.get_all("Supplier", fields=["name"], limit=2)
	companies = frappe.get_all("Company", fields=["name"], limit=1)
	items = frappe.get_all("Item", filters={"is_stock_item": 1}, fields=["name"], limit=2)
	if not suppliers or not companies or not items:
		return {"error": "站点中需要至少 2 个 Supplier、1 个 Company、2 个库存物料才能运行批量测试"}
	company = companies[0].name
	order_data_list = []
	for i in range(2):
		sup = suppliers[i % len(suppliers)].name
		it = items[i % len(items)].name
		item_doc = frappe.get_cached_doc("Item", it)
		order_data_list.append({
			"supplier": sup,
			"company": company,
			"transaction_date": getdate(),
			"schedule_date": getdate(),
			"items": [
				{
					"item_code": it,
					"item_name": item_doc.item_name or it,
					"qty": 1,
					"rate": 10,
					"schedule_date": getdate(),
				}
			],
		})
	result = save_purchase_orders(order_data_list=order_data_list)
	if result.get("error"):
		return result
	return result
