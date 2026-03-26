# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""Item 扩展属性写入：主表 br_* 字段、业务子表、默认仓库（item_defaults）。
供 BOM 画布与按编码更新白名单等共用。"""

from __future__ import unicode_literals

import frappe

# Item 主表可从 item_attrs / payload 写入的字段
ITEM_ATTRS_MAIN_FIELDS = (
	"br_packing_qty",
	"br_turnover",
	"br_carton_spec",
	"br_volume",
	"br_carton_length",
	"br_carton_width",
	"br_carton_height",
	"br_supplier",
	"br_price",
	"br_quality_inspection",
	"br_has_mark",
	"br_mark_document",
	"br_mark_document_name",
)

# 子表字段名（出现则整表覆盖）
ITEM_ATTRS_CHILD_TABLES = ("br_process_suppliers", "br_packaging_details", "br_pallet_selections")


def apply_item_attrs(item_doc, item_attrs):
	"""
	将 item_attrs 中的主表字段和子表数据应用到 Item 文档。
	不执行 save，由调用方负责。
	"""
	if not item_attrs or not isinstance(item_attrs, dict):
		return

	for k in ITEM_ATTRS_MAIN_FIELDS:
		if k not in item_attrs:
			continue
		val = item_attrs[k]
		if val is not None and (not isinstance(val, str) or val.strip() != ""):
			item_doc.set(k, val)

	for child_field in ITEM_ATTRS_CHILD_TABLES:
		if child_field not in item_attrs:
			continue
		rows = item_attrs[child_field]
		if not isinstance(rows, (list, tuple)):
			continue
		item_doc.set(child_field, [])
		for row in rows:
			if isinstance(row, dict):
				item_doc.append(child_field, row)


def resolve_warehouse_name(warehouse_input, company):
	"""
	解析仓库名称。ERPNext 仓库 name 通常带公司后缀（如 半成品 - B），
	前端可能传「半成品仓库」「半成品」等。优先精确匹配，否则按 name 或 warehouse_name 匹配。
	返回实际 Warehouse name 或 None。
	"""
	if not warehouse_input or not isinstance(warehouse_input, str) or not warehouse_input.strip():
		return None
	wh = warehouse_input.strip()
	candidates = [wh]
	abbr = frappe.get_cached_value("Company", company, "abbr") or ""
	if abbr:
		candidates.append("{0} - {1}".format(wh, abbr))
	if wh.endswith("仓库") and len(wh) > 2:
		candidates.append(wh[:-2])
		if abbr:
			candidates.append("{0} - {1}".format(wh[:-2], abbr))
	for c in candidates:
		if frappe.db.exists("Warehouse", c):
			return c
	found = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": wh, "company": company, "is_group": 0},
		"name",
	)
	if found:
		return found
	if wh.endswith("仓库") and len(wh) > 2:
		found = frappe.db.get_value(
			"Warehouse",
			{"warehouse_name": wh[:-2], "company": company, "is_group": 0},
			"name",
		)
	return found


def apply_item_warehouse(item_doc, warehouse, company):
	"""
	将仓库写入 Item 的 item_defaults 子表（按公司设置 default_warehouse）。
	不执行 save，由调用方负责。
	"""
	if not warehouse or not isinstance(warehouse, str) or not warehouse.strip():
		return
	wh = resolve_warehouse_name(warehouse, company)
	if not wh:
		return
	defaults = item_doc.get("item_defaults") or []
	found = False
	for d in defaults:
		if d.get("company") == company:
			d.default_warehouse = wh
			found = True
			break
	if not found:
		item_doc.append("item_defaults", {"company": company, "default_warehouse": wh})
