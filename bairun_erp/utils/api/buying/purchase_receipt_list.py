# Copyright (c) 2025, Bairun and contributors
# 采购明细列表接口：数据源为已提交的收货单（Purchase Receipt）中「已入库数量 > 0」的子表行扁平列表。
#
# 接口: get_purchase_receipt_details_list
# 请求方式: POST，Content-Type: application/json

from __future__ import unicode_literals

import json

import frappe


def _parse_params(kwargs):
	"""从 kwargs 或 json_data 解析 filters, order_by, limit_start, limit_page_length 及 search_*。"""
	params = {
		"filters": [],
		"order_by": "posting_date desc, receipt_name asc, idx asc",
		"limit_start": 0,
		"limit_page_length": 50,
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
	params["order_by"] = jd.get("order_by") or params["order_by"]
	params["limit_start"] = jd.get("limit_start", 0)
	params["limit_page_length"] = jd.get("limit_page_length", 50)
	params["search_customer_order"] = jd.get("search_customer_order")
	params["search_supplier"] = jd.get("search_supplier")
	params["search_item_name"] = jd.get("search_item_name")
	return params


def _flt(val, default=0):
	try:
		return float(val) if val is not None else default
	except (TypeError, ValueError):
		return default


@frappe.whitelist()
def get_purchase_receipt_details_list(**kwargs):
	"""
	采购明细列表：返回已提交收货单中「已入库数量 > 0」的收货单行扁平列表。
	数据源: Purchase Receipt (docstatus=1) + Purchase Receipt Item，仅含 received_qty > 0 的行。

	POST json_data:
	  filters: list（可选）
	  order_by: str（可选），默认 posting_date desc, receipt_name asc, idx asc
	  limit_start: int（可选），分页起始下标
	  limit_page_length: int（可选），每页条数；0 表示不分页
	  search_customer_order: str（可选），销售订单号模糊
	  search_supplier: str（可选），供应商编码/名称模糊
	  search_item_name: str（可选），物料名称模糊

	返回: { "message": [ 明细行对象, ... ], "total_count": 总条数 }
	"""
	params = _parse_params(kwargs)
	order_by = (params.get("order_by") or "posting_date desc, receipt_name asc, idx asc").strip()
	limit_start = int(params.get("limit_start", 0))
	limit_page_length = params.get("limit_page_length", 50)
	try:
		limit_page_length = int(limit_page_length)
	except (TypeError, ValueError):
		limit_page_length = 50
	use_limit = limit_page_length and limit_page_length > 0
	if not use_limit:
		limit_page_length = None

	pr_meta = frappe.get_meta("Purchase Receipt")
	item_meta = frappe.get_meta("Purchase Receipt Item")
	has_pr_customer_order = bool(pr_meta.get_field("customer_order"))
	has_item_warehouse_slot = bool(item_meta.get_field("warehouse_slot"))

	# SELECT: 主表 + 子表字段，已入库行 (received_qty > 0 或 qty > 0)
	select_parts = [
		"pr.name as receipt_name",
		"item.purchase_order as purchase_order",
		"item.item_code as item_code",
		"item.item_name as item_name",
		"item.qty as order_qty",
		"IFNULL(item.received_qty, item.qty) as received_qty",
		"item.rate as rate",
		"item.amount as amount",
		"IFNULL(item.billed_amt, 0) as billed_amt",
		"item.warehouse as warehouse",
		"item.idx as idx",
		"pr.supplier as supplier",
		"pr.supplier_name as supplier_name",
		"pr.posting_date as posting_date",
	]
	if has_pr_customer_order:
		select_parts.append("pr.customer_order as customer_order")
	else:
		select_parts.append("item.sales_order as customer_order")
	if has_item_warehouse_slot:
		select_parts.append("item.warehouse_slot as warehouse_slot")
	else:
		select_parts.append("NULL as warehouse_slot")

	conditions = [
		"pr.docstatus = 1",
		"(IFNULL(item.received_qty, item.qty) > 0)",
	]
	values = []

	search_co = (params.get("search_customer_order") or "").strip()
	search_sup = (params.get("search_supplier") or "").strip()
	search_item = (params.get("search_item_name") or "").strip()
	if search_co:
		if has_pr_customer_order:
			conditions.append("(pr.customer_order LIKE %s OR item.sales_order LIKE %s)")
			values.extend(["%" + search_co + "%", "%" + search_co + "%"])
		else:
			conditions.append("item.sales_order LIKE %s")
			values.append("%" + search_co + "%")
	if search_sup:
		conditions.append("(pr.supplier LIKE %s OR pr.supplier_name LIKE %s)")
		values.extend(["%" + search_sup + "%", "%" + search_sup + "%"])
	if search_item:
		conditions.append("(item.item_name LIKE %s OR item.item_code LIKE %s)")
		values.extend(["%" + search_item + "%", "%" + search_item + "%"])

	where_sql = " AND ".join(conditions)
	# order_by 中 receipt_name -> pr.name, posting_date -> pr.posting_date, idx -> item.idx
	order_sql = (
		order_by.replace("receipt_name", "pr.name")
		.replace("posting_date", "pr.posting_date")
		.replace("idx", "item.idx")
	)

	base_sql = """
		SELECT {}
		FROM `tabPurchase Receipt` pr
		INNER JOIN `tabPurchase Receipt Item` item ON item.parent = pr.name
		WHERE {}
	""".format(", ".join(select_parts), where_sql)

	count_sql = (
		"SELECT COUNT(*) AS cnt FROM (`tabPurchase Receipt` pr "
		"INNER JOIN `tabPurchase Receipt Item` item ON item.parent = pr.name) WHERE " + where_sql
	)
	total_count = 0
	try:
		res = frappe.db.sql(count_sql, values, as_dict=True)
		if res:
			total_count = int(res[0].get("cnt", 0))
	except Exception:
		pass

	data_sql = base_sql + " ORDER BY " + order_sql
	if use_limit:
		data_sql += " LIMIT %s, %s"
		run_values = values + [limit_start, limit_page_length]
	else:
		run_values = values

	rows = frappe.db.sql(data_sql, run_values, as_dict=True)
	out = []
	for r in rows:
		order_qty = _flt(r.get("order_qty"))
		received_qty = _flt(r.get("received_qty"))
		outstanding_qty = max(0, order_qty - received_qty)
		amount = _flt(r.get("amount"))
		billed_amt = _flt(r.get("billed_amt"))
		invoiced_percent = (
			round(billed_amt / amount * 100) if amount and amount > 0 else 0
		)
		posting_date = r.get("posting_date")
		if posting_date:
			posting_date = posting_date.strftime("%Y-%m-%d") if hasattr(posting_date, "strftime") else str(posting_date)

		row = {
			"receipt_name": r.get("receipt_name"),
			"purchase_order": r.get("purchase_order"),
			"customer_order": r.get("customer_order"),
			"supplier": r.get("supplier"),
			"supplier_name": r.get("supplier_name"),
			"posting_date": posting_date,
			"item_code": r.get("item_code"),
			"item_name": r.get("item_name"),
			"order_qty": order_qty,
			"received_qty": received_qty,
			"outstanding_qty": outstanding_qty,
			"rate": _flt(r.get("rate")),
			"amount": amount,
			"billed_amt": billed_amt,
			"invoiced_percent": invoiced_percent,
			"warehouse": r.get("warehouse"),
			"warehouse_slot": r.get("warehouse_slot"),
			"rowKey": "{}-{}".format(r.get("receipt_name") or "", r.get("idx") or (len(out) + 1)),
		}
		out.append(row)

	frappe.response["message"] = out
	frappe.response["total_count"] = total_count
	return
