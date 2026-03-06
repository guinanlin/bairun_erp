# Copyright (c) 2025, Bairun and contributors
# 采购订单列表接口：专用 API 返回采购订单列表，结构为 header（主表）+ lines（子表明细）。
#
# 接口路径: /api/method/bairun_erp.utils.api.buying.purchase_order_list.get_purchase_order_list
# 请求方式: POST，Content-Type: application/json
# 请求体: { "json_data": { "filters": [], "order_by": "creation desc", "limit_start": 0, "limit_page_length": 100 } }
# 响应: { "message": [ { "header": {...}, "lines": [ {...}, ... ] }, ... ] }

from __future__ import unicode_literals

import json

import frappe


# 列表返回的字段（主表可直接取的）
_PO_LIST_FIELDS = [
	"name",
	"title",
	"supplier",
	"supplier_name",
	"transaction_date",
	"schedule_date",
	"company",
	"status",
	"per_billed",
	"per_received",
	"total_qty",
	"grand_total",
	"currency",
	"set_warehouse",
	"creation",
	"owner",
]

# 主表可能存在的自定义字段（若不存在则跳过）
_PO_OPTIONAL_FIELDS = ("customer_order", "custom_purchase_type", "warehouse_slot", "warehouse")


def _parse_params(kwargs):
	"""从 kwargs 或 json_data 解析 filters, order_by, limit_start, limit_page_length 及可选 search_*。"""
	params = {
		"filters": [],
		"order_by": "creation desc",
		"limit_start": 0,
		"limit_page_length": 100,
		"search_customer_order": None,
		"search_supplier": None,
		"search_item_name": None,
	}
	jd = kwargs.get("json_data")
	if jd is None:
		jd = kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return params
	if not isinstance(jd, dict):
		return params

	params["filters"] = jd.get("filters") or []
	params["order_by"] = jd.get("order_by") or "creation desc"
	params["limit_start"] = jd.get("limit_start", 0)
	params["limit_page_length"] = jd.get("limit_page_length", 100)
	params["search_customer_order"] = jd.get("search_customer_order") or jd.get("search_customer_order_no")
	params["search_supplier"] = jd.get("search_supplier")
	params["search_item_name"] = jd.get("search_item_name")
	return params


def _get_po_meta_fieldnames():
	"""返回 Purchase Order 主表实际存在的字段名集合（含自定义）。"""
	meta = frappe.get_meta("Purchase Order")
	return {f.fieldname for f in meta.get("fields")}


def _build_filters_and_or_filters(params, existing_filters):
	"""根据 params 中的 search_* 构建 or_filters（LIKE），并保持 filters 为 list of lists。"""
	filters = list(existing_filters) if existing_filters else []
	or_filters = []

	search_sup = (params.get("search_supplier") or "").strip()
	if search_sup:
		or_filters.append(["Purchase Order", "supplier", "like", "%" + search_sup + "%"])
		or_filters.append(["Purchase Order", "supplier_name", "like", "%" + search_sup + "%"])

	return filters, or_filters


def _get_item_aggregates(po_names):
	"""批量获取每个 PO 的 item_name 汇总（顿号拼接）和 customer_order（来自子表 sales_order，去重拼接）。"""
	if not po_names:
		return {}, {}

	rows = frappe.db.sql(
		"""
		SELECT parent, item_name, sales_order
		FROM `tabPurchase Order Item`
		WHERE parent IN %s
		ORDER BY parent, idx
		""",
		[po_names],
		as_dict=True,
	)

	item_names_by_po = {}
	customer_orders_by_po = {}
	for r in rows:
		parent = r.get("parent")
		if not parent:
			continue
		names = item_names_by_po.setdefault(parent, [])
		item_name = (r.get("item_name") or "").strip()
		if item_name and item_name not in names:
			names.append(item_name)
		so = (r.get("sales_order") or "").strip()
		if so:
			orders = customer_orders_by_po.setdefault(parent, [])
			if so not in orders:
				orders.append(so)

	def join_unique(lst):
		return "、".join(lst) if lst else None

	for k in list(item_names_by_po.keys()):
		item_names_by_po[k] = join_unique(item_names_by_po[k])
	for k in list(customer_orders_by_po.keys()):
		customer_orders_by_po[k] = join_unique(customer_orders_by_po[k])

	return item_names_by_po, customer_orders_by_po


# 子表明细返回字段（与需求文档第五节展开行一致）
_PO_LINE_FIELDS = ("name", "item_code", "item_name", "description", "qty", "rate", "amount", "schedule_date")


def _get_po_lines(po_names):
	"""批量获取每个采购订单的子表明细 lines（Purchase Order Item）。返回 { po_name: [ line_dict, ... ] }。"""
	if not po_names:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT parent, name, item_code, item_name, description, qty, rate, amount, schedule_date
		FROM `tabPurchase Order Item`
		WHERE parent IN %s
		ORDER BY parent, idx
		""",
		[po_names],
		as_dict=True,
	)

	lines_by_po = {}
	for r in rows:
		parent = r.get("parent")
		if not parent:
			continue
		line = {k: r.get(k) for k in _PO_LINE_FIELDS}
		lines_by_po.setdefault(parent, []).append(line)
	return lines_by_po


def _apply_search_item_and_customer_order(rows, params, item_names_by_po, customer_orders_by_po):
	"""若传了 search_item_name 或 search_customer_order，在内存中过滤列表。每项为 { "header": {...}, "lines": [...] }。"""
	search_item = (params.get("search_item_name") or "").strip().lower()
	search_co = (params.get("search_customer_order") or "").strip().lower()
	if not search_item and not search_co:
		return rows

	out = []
	for item in rows:
		header = item.get("header") or {}
		po_name = header.get("name")
		item_name = item_names_by_po.get(po_name) or header.get("item_name") or ""
		customer_order = customer_orders_by_po.get(po_name) or header.get("customer_order") or ""
		if search_item and search_item not in (item_name or "").lower():
			continue
		if search_co and search_co not in (customer_order or "").lower():
			continue
		out.append(item)
	return out


@frappe.whitelist()
def get_purchase_order_list(**kwargs):
	"""
	采购订单列表接口。
	请求体（POST json_data）:
	  filters: list（可选），与 Frappe get_list 一致，如 [["status", "=", "To Receive"]]
	  order_by: str（可选），默认 "creation desc"
	  limit_start: int（可选），分页起始下标
	  limit_page_length: int（可选），每页条数；0 表示不分页
	  search_customer_order: str（可选），按销售订单号模糊过滤
	  search_supplier: str（可选），按供应商编码/名称模糊过滤
	  search_item_name: str（可选），按物料名称模糊过滤（子表汇总后过滤）

	返回: { "message": [ { "header": {...}, "lines": [ {...}, ... ] }, ... ] }，header 为主表字段，lines 为子表明细。
	"""
	params = _parse_params(kwargs)
	limit_start = int(params["limit_start"])
	limit_page_length = params["limit_page_length"]
	try:
		limit_page_length = int(limit_page_length)
	except (TypeError, ValueError):
		limit_page_length = 100
	if limit_page_length == 0:
		limit_page_length = None

	filters = params["filters"]
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except json.JSONDecodeError:
			filters = []
	filters, or_filters = _build_filters_and_or_filters(params, filters)

	meta_fields = _get_po_meta_fieldnames()
	# name 为主键，meta.fields 中可能不包含；creation、owner 为常用列，优先带上
	requested = ["name"]
	requested += [f for f in _PO_LIST_FIELDS if f in meta_fields and f not in requested]
	for f in _PO_OPTIONAL_FIELDS:
		if f in meta_fields and f not in requested:
			requested.append(f)

	order_by = params["order_by"] or "creation desc"

	po_list = frappe.get_list(
		"Purchase Order",
		fields=requested,
		filters=filters,
		or_filters=or_filters if or_filters else None,
		order_by=order_by,
		limit_start=limit_start,
		limit_page_length=limit_page_length,
		ignore_permissions=False,
	)

	po_names = [r.get("name") for r in po_list if r.get("name")]
	item_names_by_po, customer_orders_by_po = _get_item_aggregates(po_names)
	lines_by_po = _get_po_lines(po_names)

	result = []
	for r in po_list:
		po_name = r.get("name")
		header = dict(r)
		header["item_name"] = item_names_by_po.get(po_name)
		if "customer_order" not in header or header.get("customer_order") is None:
			header["customer_order"] = customer_orders_by_po.get(po_name)
		if header.get("set_warehouse") and not header.get("warehouse"):
			header["warehouse"] = header["set_warehouse"]
		lines = lines_by_po.get(po_name) or []
		result.append({"header": header, "lines": lines})

	result = _apply_search_item_and_customer_order(
		result, params, item_names_by_po, customer_orders_by_po
	)

	return result
