# Copyright (c) 2025, Bairun and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe

from bairun_erp.utils.api.material.item import (
	_ensure_supplier_items,
	_get_default_leaf_name,
	_parse_suppliers,
)


def _validate_suppliers_exist_for_service(suppliers):
	"""校验传入的供应商均在系统中存在；若有不存在则抛出，不允许添加服务。"""
	parsed = _parse_suppliers(suppliers)
	if not parsed:
		return
	invalid = []
	for row in parsed:
		supplier = (row.get("supplier") or "").strip()
		if not supplier:
			continue
		if not frappe.db.exists("Supplier", supplier):
			invalid.append(supplier)
	if invalid:
		frappe.throw(
			f"以下供应商不存在：{', '.join(invalid)}，不允许添加服务。",
			title="供应商无效",
		)


def _build_service_item_response(doc, include_supplier_name=False):
	"""统一构造返回结构。include_supplier_name 为 True 时带出 supplier_name（用于只读接口）。
	若 Item 有纸箱长宽高字段（br_carton_length/width/height，单位厘米），则一并返回，并返回体积 br_volume_m3（立方米，= 长*宽*高/1e6）。
	"""
	supplier_items = []
	for row in (doc.get("supplier_items") or []):
		entry = {
			"supplier": row.supplier,
			"supplier_part_no": getattr(row, "supplier_part_no", None) or "",
			"custom_price": getattr(row, "custom_price", None),
			"custom_isinvoice": getattr(row, "custom_isinvoice", None),
		}
		if include_supplier_name and row.supplier:
			supplier_name = frappe.db.get_value("Supplier", row.supplier, "supplier_name")
			entry["supplier_name"] = supplier_name or row.supplier
		supplier_items.append(entry)

	out = {
		"item_code": doc.item_code,
		"item_name": doc.item_name,
		"item_group": doc.item_group,
		"stock_uom": doc.stock_uom,
		"description": getattr(doc, "description", None) or "",
		"supplier_items": supplier_items,
	}

	# 纸箱长宽高（厘米）与体积（立方米）：有则返回，无则 None
	l = _float_or_none(getattr(doc, "br_carton_length", None))
	w = _float_or_none(getattr(doc, "br_carton_width", None))
	h = _float_or_none(getattr(doc, "br_carton_height", None))
	out["br_carton_length"] = l
	out["br_carton_width"] = w
	out["br_carton_height"] = h
	if l is not None and w is not None and h is not None:
		out["br_volume_m3"] = (l / 100.0) * (w / 100.0) * (h / 100.0)
	else:
		out["br_volume_m3"] = None
	return out


def _float_or_none(val):
	"""若 val 可转为 float 则返回 float，否则返回 None。"""
	if val is None or val == "":
		return None
	try:
		return float(val)
	except (TypeError, ValueError):
		return None


@frappe.whitelist()
def add_service_item(
	item_code: str | None = None,
	item_name: str | None = None,
	item_group: str | None = None,
	stock_uom: str | None = None,
	suppliers: str | list | None = None,
	description: str | None = None,
):
	"""
	添加或更新服务：无则新增，有则更新（含供应商及单价/是否开票）。
	服务类 Item 无长宽高；供应商可传多个。更新时若传 suppliers 则覆盖供应商明细，不传则保留原有。

	参数:
		item_code: 可选。物料编码。与 item_name 至少传一个；若只传 item_name 则用 item_name 作为 item_code。
		item_name: 可选。物料名称。若只传 item_code 则 item_name 默认等于 item_code；更新时未传则保留原值。
		item_group: 可选。物料组。不传默认「服务」或保留原值。
		stock_uom: 可选。库存单位，默认 "Nos" 或保留原值。
		suppliers: 可选。供应商列表，JSON 或 list，每项 {supplier, custom_price?, custom_isinvoice?, supplier_part_no?}。
		           新增时不传则用系统中全部供应商；传 [] 则零家供应商。
		           更新时不传则不修改供应商明细；传了（含 []）则整表替换，前端任意增删改后提交当前全量列表即可。
		description: 可选。描述；更新时未传则保留原值。

	返回:
		{"item_code": "...", "item_name": "...", "item_group": "...", "supplier_items": [...], ...}
	"""
	item_code = (item_code or "").strip()
	item_name = (item_name or "").strip()

	if not item_code and not item_name:
		frappe.throw("item_code 与 item_name 至少需要传一个")

	if not item_code:
		item_code = item_name
	if not item_name:
		item_name = item_code

	# 若传入了供应商列表，先校验全部存在
	_validate_suppliers_exist_for_service(suppliers)

	exists = frappe.db.exists("Item", item_code)

	if exists:
		# 更新：修改基本信息，若传了 suppliers 则覆盖供应商明细
		doc = frappe.get_doc("Item", item_code)
		if (item_name or "").strip():
			doc.item_name = item_name
		if (item_group or "").strip():
			doc.item_group = (item_group or "").strip()
		if (stock_uom or "").strip():
			doc.stock_uom = (stock_uom or "").strip() or "Nos"
		if description is not None:
			doc.description = (description or "").strip() or ""

		# 传了 suppliers（含 JSON 空数组 []）即整表同步：增删改均由前端当前列表唯一决定
		if suppliers is not None:
			doc.supplier_items = []
			_ensure_supplier_items(doc, suppliers)
		doc.save(ignore_permissions=True)
		return _build_service_item_response(doc, include_supplier_name=False)

	# 新增
	if (item_group or "").strip():
		item_group_val = (item_group or "").strip()
	else:
		item_group_val = "服务" if frappe.db.exists("Item Group", "服务") else _get_default_leaf_name("Item Group")
	stock_uom_val = (stock_uom or "").strip() or "Nos"
	description_val = (description or "").strip() or ""

	doc_dict = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": item_group_val,
		"stock_uom": stock_uom_val,
		"is_stock_item": 0,
		"is_sales_item": 1,
	}
	if description_val:
		doc_dict["description"] = description_val

	doc = frappe.get_doc(doc_dict)
	doc.insert(ignore_permissions=True)
	_ensure_supplier_items(doc, suppliers)
	return _build_service_item_response(doc, include_supplier_name=False)


def _resolve_service_item_code(
	item_code: str | None = None,
	item_name: str | None = None,
	item_group: str | None = None,
) -> str | None:
	"""
	根据 item_code 或 item_name 解析到服务 Item 的 name（item_code）。
	与 add_service_item 约定一致：只传 item_name 时先按 item_code=item_name 查，再按 item_name 查。
	若未找到则返回 None。
	"""
	item_code = (item_code or "").strip()
	item_name = (item_name or "").strip()
	item_group = (item_group or "").strip()

	if item_code:
		if frappe.db.exists("Item", item_code):
			if item_group:
				doc_item_group = frappe.db.get_value("Item", item_code, "item_group")
				if doc_item_group != item_group:
					return None
			return item_code
		return None

	if not item_name:
		return None

	# 先按 add_service_item 约定：item_code = item_name 创建的
	if frappe.db.exists("Item", item_name):
		doc = frappe.db.get_value("Item", item_name, ["item_group"], as_dict=True)
		if not item_group or (doc and doc.get("item_group") == item_group):
			return item_name

	# 再按 item_name 字段查（可能 item_code 与 item_name 不同）
	filters = {"item_name": item_name}
	if item_group:
		filters["item_group"] = item_group
	found = frappe.get_all(
		"Item",
		filters=filters,
		fields=["name"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if found:
		return found[0]["name"]
	return None


@frappe.whitelist()
def get_service_item(
	item_code: str | None = None,
	item_name: str | None = None,
	item_group: str | None = None,
):
	"""
	按采购类目获取服务 Item 及供应商明细（只读）。
	用于采购价格页初始加载或切换类目时拉取该类目下已配置的供应商与单价。
	也用于箱规选择：传 item_code 可查任意 Item（含纸箱），返回供应商列表及纸箱长宽高、体积（立方米）。

	参数:
		item_code: 可选。物料编码。与 item_name 至少传一个。
		item_name: 可选。物料名称。前端采购类目（如「注塑」「UV镀」）即对应 item_name。
		item_group: 可选。物料组，用于缩小范围或校验。

	返回:
		含 item_code、item_name、item_group、stock_uom、description、supplier_items（含 supplier、
		supplier_name、supplier_part_no、custom_price、custom_isinvoice）；
		若 Item 有纸箱长宽高（br_carton_*，单位厘米），则含 br_carton_length、br_carton_width、
		br_carton_height、br_volume_m3（体积，立方米，= 长*宽*高/1e6）；无则上述字段为 null。
		若该类目尚未建服务 Item（从未做过供应商审核），返回 200，message 中 supplier_items 为空数组，
		主表字段可为 null/空，便于前端按「未配置」展示空列表。
	"""
	item_code = (item_code or "").strip()
	item_name = (item_name or "").strip()

	if not item_code and not item_name:
		frappe.throw("item_code 与 item_name 至少需要传一个")

	resolved = _resolve_service_item_code(
		item_code=item_code or None,
		item_name=item_name or None,
		item_group=(item_group or "").strip() or None,
	)

	if not resolved:
		# 该类目尚未配置服务 Item，返回方案 B：空 supplier_items，主表用请求的 item_name
		return {
			"item_code": None,
			"item_name": item_name or None,
			"item_group": None,
			"stock_uom": None,
			"description": None,
			"supplier_items": [],
			"br_carton_length": None,
			"br_carton_width": None,
			"br_carton_height": None,
			"br_volume_m3": None,
		}

	doc = frappe.get_doc("Item", resolved)
	return _build_service_item_response(doc, include_supplier_name=True)
