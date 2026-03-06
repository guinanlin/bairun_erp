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


def _build_service_item_response(doc):
	"""统一构造返回结构。"""
	return {
		"item_code": doc.item_code,
		"item_name": doc.item_name,
		"item_group": doc.item_group,
		"stock_uom": doc.stock_uom,
		"description": getattr(doc, "description", None) or "",
		"supplier_items": [
			{
				"supplier": row.supplier,
				"supplier_part_no": getattr(row, "supplier_part_no", None) or "",
				"custom_price": getattr(row, "custom_price", None),
				"custom_isinvoice": getattr(row, "custom_isinvoice", None),
			}
			for row in (doc.get("supplier_items") or [])
		],
	}


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
		           新增时不传则用系统中全部供应商；更新时不传则不修改供应商明细，传了则覆盖更新。
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

		parsed = _parse_suppliers(suppliers)
		if parsed:
			doc.supplier_items = []
			_ensure_supplier_items(doc, suppliers)
		doc.save(ignore_permissions=True)
		return _build_service_item_response(doc)

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
	return _build_service_item_response(doc)
