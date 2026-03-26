# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""按 item_code 局部更新 Item 扩展属性（白名单字段）。"""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import flt

from bairun_erp.utils.api.material.item_attrs_apply import (
	ITEM_ATTRS_CHILD_TABLES,
	ITEM_ATTRS_MAIN_FIELDS,
	apply_item_attrs,
	apply_item_warehouse,
	resolve_warehouse_name,
)

# 除 br_* 与子表外，允许的标准/业务顶层字段
_EXTRA_ALLOWED = frozenset({"item_name", "warehouse"})

ALLOWED_UPDATE_KEYS = _EXTRA_ALLOWED | set(ITEM_ATTRS_MAIN_FIELDS) | set(ITEM_ATTRS_CHILD_TABLES)

_CHILD_PRICE_FIELDS = ("br_price_one", "br_price_two", "br_price_three")


def _parse_kwargs_json_data(kwargs):
	"""兼容 /api/method 直传参数与 json_data 包装参数。"""
	jd = kwargs.get("json_data")
	if jd is None:
		jd = {k: v for k, v in kwargs.items() if k not in ("cmd",)}
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except (TypeError, ValueError):
			jd = {}
	if not isinstance(jd, dict):
		jd = {}
	return jd


def _get_default_company():
	company = frappe.defaults.get_user_default("Company")
	if company:
		return company
	companies = frappe.get_all("Company", limit=1)
	if not companies:
		frappe.throw("No Company found")
	return companies[0]["name"]


def _validate_payload(payload):
	"""
	校验白名单内的数值。返回 (True, None) 或 (False, message)。
	"""
	if "br_packing_qty" in payload:
		q = payload["br_packing_qty"]
		if q is not None and q != "":
			try:
				if flt(q) <= 0:
					return False, "br_packing_qty 必须大于 0"
			except Exception:
				return False, "br_packing_qty 必须为数字"

	if "br_price" in payload:
		p = payload["br_price"]
		if p is not None and p != "":
			try:
				if flt(p) < 0:
					return False, "br_price 不能为负数"
			except Exception:
				return False, "br_price 必须为数字"

	for cf in ITEM_ATTRS_CHILD_TABLES:
		if cf not in payload:
			continue
		rows = payload[cf]
		if not isinstance(rows, (list, tuple)):
			return False, "{0} 必须为数组".format(cf)
		for row in rows:
			if not isinstance(row, dict):
				continue
			for pk in _CHILD_PRICE_FIELDS:
				if pk not in row:
					continue
				v = row[pk]
				if v is None or v == "":
					continue
				try:
					if flt(v) < 0:
						return False, "{0} 中行字段 {1} 不能为负数".format(cf, pk)
				except Exception:
					return False, "{0} 中行字段 {1} 必须为数字".format(cf, pk)

	return True, None


def _filter_allowed(data):
	out = {}
	for k, v in data.items():
		if k in ALLOWED_UPDATE_KEYS:
			out[k] = v
	return out


def _has_updatable_content(payload):
	if not payload:
		return False
	if "item_name" in payload:
		return True
	if "warehouse" in payload:
		w = payload.get("warehouse")
		if w is not None and isinstance(w, str) and w.strip():
			return True
	for k in ITEM_ATTRS_MAIN_FIELDS:
		if k in payload:
			return True
	for k in ITEM_ATTRS_CHILD_TABLES:
		if k in payload:
			return True
	return False


@frappe.whitelist()
def update_item_properties_by_item_code(item_code=None, **kwargs):
	"""
	按物料编码局部更新 Item 属性（仅后端白名单字段，缺省不修改）。

	允许字段（Frappe 字段名）：
	- item_name
	- warehouse（写入当前用户默认公司下的 item_defaults.default_warehouse）
	- 主表：br_packing_qty, br_turnover, br_carton_spec, br_volume,
	  br_carton_length, br_carton_width, br_carton_height,
	  br_supplier, br_price, br_quality_inspection, br_has_mark,
	  br_mark_document, br_mark_document_name
	- 子表（传入则整表覆盖）：br_process_suppliers, br_packaging_details, br_pallet_selections

	参数:
		item_code: 物料编码（也可放在 json_data 内）
		json_data: JSON 字符串或 dict，可含上述字段及 item_code

	返回:
		{"success": true, "message": "...", "data": {"item_code", "modified"}}
		{"success": false, "message": "..."}

	bench execute 示例:
		bench --site site2.local execute bairun_erp.utils.api.material.item_properties_update.update_item_properties_by_item_code --kwargs '{"item_code":"STO-ITEM-2025-00001","json_data":"{\\"item_name\\":\\"新名称\\",\\"br_packing_qty\\":100}"}'
	"""
	merged = _parse_kwargs_json_data(kwargs)
	if item_code is None and merged.get("item_code") is not None:
		item_code = merged.get("item_code")
	item_code = (item_code or "").strip()
	if not item_code:
		return {"success": False, "message": "item_code 不能为空"}

	# 构建更新体：json_data 内字段 + 顶层 kwargs 中允许的键（不含 item_code）
	payload_source = dict(merged)
	payload_source.pop("item_code", None)
	for k, v in kwargs.items():
		if k in ("cmd", "json_data"):
			continue
		if k == "item_code":
			continue
		payload_source[k] = v

	payload = _filter_allowed(payload_source)

	if not _has_updatable_content(payload):
		return {
			"success": False,
			"message": "没有可更新的白名单字段",
		}

	ok, err = _validate_payload(payload)
	if not ok:
		return {"success": False, "message": err}

	if not frappe.db.exists("Item", item_code):
		return {"success": False, "message": "物料不存在"}

	item_doc = frappe.get_doc("Item", item_code)
	if not frappe.has_permission("Item", "write", doc=item_doc):
		return {"success": False, "message": "无权限更新该物料"}

	company = _get_default_company()

	if "warehouse" in payload:
		wh_raw = payload.get("warehouse")
		if wh_raw is not None and isinstance(wh_raw, str) and wh_raw.strip():
			if not resolve_warehouse_name(wh_raw.strip(), company):
				return {"success": False, "message": "warehouse 无法匹配到系统仓库"}

	if "item_name" in payload:
		nm = payload.get("item_name")
		if nm is None or (isinstance(nm, str) and not nm.strip()):
			return {"success": False, "message": "item_name 不能为空"}
		item_doc.item_name = (nm if isinstance(nm, str) else str(nm)).strip()

	attr_only = {k: v for k, v in payload.items() if k not in ("item_name", "warehouse")}
	if attr_only:
		apply_item_attrs(item_doc, attr_only)

	if "warehouse" in payload:
		wh_raw = payload.get("warehouse")
		if wh_raw is not None and isinstance(wh_raw, str) and wh_raw.strip():
			apply_item_warehouse(item_doc, wh_raw.strip(), company)

	try:
		item_doc.save()
	except frappe.exceptions.ValidationError as e:
		return {"success": False, "message": str(e)}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "update_item_properties_by_item_code")
		return {"success": False, "message": str(e)}

	item_doc.reload()
	return {
		"success": True,
		"message": "updated",
		"data": {
			"item_code": item_doc.item_code or item_doc.name,
			"modified": item_doc.modified,
		},
	}
