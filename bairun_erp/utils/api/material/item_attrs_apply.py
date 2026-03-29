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
	# 作业指导书：存 URL（建议前端上传后传公开/可访问链接，勿传超大 Data URL）
	"custom_work_instruction_url",
)

# 子表字段名（出现则整表覆盖）
ITEM_ATTRS_CHILD_TABLES = ("br_process_suppliers", "br_packaging_details", "br_pallet_selections")

# BOM 画布下拉等「展示名」→ 优先尝试的 warehouse_name 词干（再拼「词干 - 公司缩写」）
_WAREHOUSE_UI_TO_NAME_STEMS = {
	"成品仓库": ("成品",),
	"半成品仓库": ("半成品",),
	"毛坯仓库": ("毛坯",),
	"原材料仓库": ("原材料仓", "原材料"),
	"在制品仓库": ("在制品",),
	"库存仓库": ("库存仓", "库存"),
}


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
	candidates = []
	abbr = frappe.get_cached_value("Company", company, "abbr") or ""

	for stem in _WAREHOUSE_UI_TO_NAME_STEMS.get(wh, ()):
		candidates.append(stem)
		if abbr:
			candidates.append("{0} - {1}".format(stem, abbr))

	candidates.append(wh)
	if abbr:
		candidates.append("{0} - {1}".format(wh, abbr))
	if wh.endswith("仓库") and len(wh) > 2:
		candidates.append(wh[:-2])
		if abbr:
			candidates.append("{0} - {1}".format(wh[:-2], abbr))

	seen = set()
	for c in candidates:
		if not c or c in seen:
			continue
		seen.add(c)
		if not frappe.db.exists("Warehouse", c):
			continue
		row = frappe.db.get_value(
			"Warehouse",
			c,
			["company", "disabled", "is_group"],
			as_dict=True,
		)
		if (
			row
			and row.company == company
			and not row.get("is_group")
			and not row.get("disabled")
		):
			return c
	found = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": wh, "company": company, "is_group": 0, "disabled": 0},
		"name",
	)
	if found:
		return found
	if wh.endswith("仓库") and len(wh) > 2:
		found = frappe.db.get_value(
			"Warehouse",
			{"warehouse_name": wh[:-2], "company": company, "is_group": 0, "disabled": 0},
			"name",
		)
		if found:
			return found
	for stem in _WAREHOUSE_UI_TO_NAME_STEMS.get(wh, ()):
		found = frappe.db.get_value(
			"Warehouse",
			{"warehouse_name": stem, "company": company, "is_group": 0, "disabled": 0},
			"name",
		)
		if found:
			return found
	return None


def apply_item_warehouse(item_doc, warehouse, company, propagate_to_all_item_defaults=True):
	"""
	将仓库写入 Item 的 item_defaults 子表（default_warehouse）。
	- propagate_to_all_item_defaults=True：对已有 item_defaults 的**每个公司**分别解析展示名并写入
	  （该公司下无法解析则保持该行原默认仓不变）；若尚无子表行，则为 company 追加一行。
	- propagate_to_all_item_defaults=False：仅维护参数 company 对应的一行（与旧行为一致）。
	主调方须保证 company 对应展示名已能解析，否则应先校验再调用。
	不执行 save，由调用方负责。
	"""
	if not warehouse or not isinstance(warehouse, str) or not warehouse.strip():
		return
	disp = warehouse.strip()
	defaults = item_doc.get("item_defaults") or []

	if not propagate_to_all_item_defaults:
		wh = resolve_warehouse_name(disp, company)
		if not wh:
			return
		found = False
		for d in defaults:
			if d.get("company") == company:
				d.default_warehouse = wh
				found = True
				break
		if not found:
			item_doc.append("item_defaults", {"company": company, "default_warehouse": wh})
		return

	# 多公司：每个已有公司尽量解析并写入；必须至少写入会话主公司 company
	primary_wh = resolve_warehouse_name(disp, company)
	if not primary_wh:
		return

	companies_order = []
	seen_co = set()
	for d in defaults:
		co = d.get("company")
		if co and co not in seen_co:
			seen_co.add(co)
			companies_order.append(co)
	if company not in seen_co:
		companies_order.append(company)

	for co in companies_order:
		wname = resolve_warehouse_name(disp, co)
		if not wname:
			continue
		row_found = False
		for d in item_doc.get("item_defaults") or []:
			if d.get("company") == co:
				d.default_warehouse = wname
				row_found = True
				break
		if not row_found and co == company:
			item_doc.append("item_defaults", {"company": company, "default_warehouse": primary_wh})
