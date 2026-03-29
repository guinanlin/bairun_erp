# Copyright (c) 2025, Bairun and contributors
# 毛坯/半成品列表接口：待委外列表、已委外列表。
# 毛坯：到库=入毛坯仓 SE+PR；委外=毛坯仓→半成品仓。
# 半成品：到库=入半成品仓（Material Transfer 毛坯→半成品，或 Material Receipt 入半成品仓，如毛坯委外编排回库 SE）；委外=半成品仓→成品仓。
# 调拨单（Stock Entry）列表汇总含草稿(docstatus=0)与已提交(1)，不含已取消(2)。
# 接口路径：bairun_erp.utils.api.stock.blank_list

from __future__ import unicode_literals

import json

import frappe


BLANK_WAREHOUSE = "毛坯 - B"
SEMI_FINISHED_WAREHOUSE = "半成品 - B"
FINISHED_WAREHOUSE = "成品 - B"

LIST_TYPE_BLANK = "blank"
LIST_TYPE_SEMI_FINISHED = "semi_finished"

# Stock Entry：草稿与已提交均参与到库/委外汇总（排除已取消）
_STE_DOCSTATUS_FOR_LIST_SQL = "se.docstatus IN (0, 1)"

# 白名单 order_by 允许的列名，防止 SQL 注入
_ORDER_COLUMNS = frozenset({
	"project_no", "item_code", "item_full_name", "warehouse",
	"received_qty", "outsourcing_qty", "posting_date",
})


def _parse_params(kwargs):
	"""从 kwargs 或 json_data 解析分页、排序、筛选参数。"""
	params = {
		"filters": [],
		"order_by": "project_no asc, item_code asc",
		"limit_start": 0,
		"limit_page_length": 50,
		"search_project_no": None,
		"search_outsourcing_supplier": None,
		"status": None,
		"list_type": LIST_TYPE_BLANK,
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
	params["order_by"] = (jd.get("order_by") or params["order_by"]).strip()
	params["limit_start"] = int(jd.get("limit_start", 0))
	params["limit_page_length"] = jd.get("limit_page_length", 50)
	params["search_project_no"] = jd.get("search_project_no")
	params["search_outsourcing_supplier"] = jd.get("search_outsourcing_supplier")
	params["status"] = jd.get("status")
	lt = (jd.get("list_type") or "").strip().lower()
	if lt == LIST_TYPE_SEMI_FINISHED:
		params["list_type"] = LIST_TYPE_SEMI_FINISHED
	else:
		params["list_type"] = LIST_TYPE_BLANK
	return params


def _flt(val, default=0):
	try:
		return float(val) if val is not None else default
	except (TypeError, ValueError):
		return default


def _date_str(d):
	if d is None:
		return None
	if hasattr(d, "strftime"):
		return d.strftime("%Y-%m-%d")
	return str(d)


def _get_project_no_from_pr(pr_name, item_code):
	"""从 PR 取销售订单号：主表 customer_order 或子表 sales_order。"""
	if not pr_name:
		return None
	pr_meta = frappe.get_meta("Purchase Receipt")
	has_co = bool(pr_meta.get_field("customer_order"))
	if has_co:
		co = frappe.db.get_value("Purchase Receipt", pr_name, "customer_order")
		if co:
			return co
	# 从 PR Item 取 sales_order（同 item_code）
	so = frappe.db.get_value(
		"Purchase Receipt Item",
		{"parent": pr_name, "item_code": item_code},
		"sales_order",
		order_by="idx asc",
	)
	return so


def _get_received_by_key(list_type=LIST_TYPE_BLANK):
	"""
	到库数。
	- blank: 入毛坯仓的 SE 行（reference_purchase_receipt 关联 PR 取销售订单）。
	- semi_finished: 入半成品仓的 SE 行：① Material Transfer 毛坯→半成品；② Material Receipt 明细目标仓为半成品（来源仓可空，如 submit_blank_outsourcing 回库草稿）。project_no 均从 SE.custom_customer_order。
	返回: dict key (project_no, item_code, warehouse) -> { "received_qty", "receipt_details" }
	"""
	if list_type == LIST_TYPE_SEMI_FINISHED:
		se_meta = frappe.get_meta("Stock Entry")
		has_customer_order = bool(se_meta.get_field("custom_customer_order"))
		select = [
			"sed.t_warehouse AS warehouse",
			"sed.item_code",
			"sed.qty",
			"se.posting_date",
			"sed.parent AS se_name",
		]
		if has_customer_order:
			select.append("se.custom_customer_order AS project_no")
		else:
			select.append("'' AS project_no")
		select_sql = ", ".join(select)
		sql_mt = """
			SELECT {}
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND {}
			WHERE se.purpose = 'Material Transfer'
			  AND sed.s_warehouse = %s AND sed.t_warehouse = %s
		""".format(select_sql, _STE_DOCSTATUS_FOR_LIST_SQL)
		sql_mr = """
			SELECT {}
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND {}
			WHERE se.purpose = 'Material Receipt'
			  AND sed.t_warehouse = %s
		""".format(select_sql, _STE_DOCSTATUS_FOR_LIST_SQL)
		rows_mt = frappe.db.sql(sql_mt, (BLANK_WAREHOUSE, SEMI_FINISHED_WAREHOUSE), as_dict=True)
		rows_mr = frappe.db.sql(sql_mr, (SEMI_FINISHED_WAREHOUSE,), as_dict=True)
		agg = {}
		for r in rows_mt + rows_mr:
			project_no = (r.get("project_no") or "").strip()
			warehouse = r.get("warehouse") or SEMI_FINISHED_WAREHOUSE
			key = (project_no, r.get("item_code"), warehouse)
			if key not in agg:
				agg[key] = {"received_qty": 0, "receipt_details": [], "warehouse_location": ""}
			qty = _flt(r.get("qty"))
			agg[key]["received_qty"] += qty
			agg[key]["receipt_details"].append({
				"receiptDate": _date_str(r.get("posting_date")),
				"receiptNo": r.get("se_name"),
				"qty": qty,
			})
		return agg

	# 毛坯到库：入毛坯仓，通过 PR 取销售订单；库位取自 PR 行 warehouse_slot（有则取最近入库的库位）
	pri_meta = frappe.get_meta("Purchase Receipt Item")
	has_pri_warehouse_slot = bool(pri_meta.get_field("warehouse_slot"))
	warehouse_slot_expr = (
		"(SELECT pri.warehouse_slot FROM `tabPurchase Receipt Item` pri "
		"WHERE pri.parent = sed.reference_purchase_receipt AND pri.item_code = sed.item_code "
		"ORDER BY pri.idx ASC LIMIT 1) AS warehouse_slot"
	) if has_pri_warehouse_slot else "NULL AS warehouse_slot"
	sql = """
		SELECT sed.parent AS se_name, sed.item_code, sed.t_warehouse AS warehouse,
		       sed.qty, sed.reference_purchase_receipt AS pr_name,
		       se.posting_date,
		       {}
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND {}
		WHERE sed.t_warehouse = %s
	""".format(warehouse_slot_expr, _STE_DOCSTATUS_FOR_LIST_SQL)
	rows = frappe.db.sql(sql, (BLANK_WAREHOUSE,), as_dict=True)
	agg = {}
	for r in rows:
		project_no = _get_project_no_from_pr(r.get("pr_name"), r.get("item_code")) or ""
		warehouse = r.get("warehouse") or BLANK_WAREHOUSE
		key = (project_no, r.get("item_code"), warehouse)
		if key not in agg:
			agg[key] = {"received_qty": 0, "receipt_details": [], "warehouse_location": "", "_location_date": None}
		qty = _flt(r.get("qty"))
		agg[key]["received_qty"] += qty
		agg[key]["receipt_details"].append({
			"receiptDate": _date_str(r.get("posting_date")),
			"receiptNo": r.get("se_name"),
			"qty": qty,
		})
		# 库位：取最近入库行的库位（按 posting_date 取最新）
		slot = (r.get("warehouse_slot") or "").strip()
		if slot:
			posting_dt = r.get("posting_date")
			cur_dt = agg[key].get("_location_date")
			if cur_dt is None or (posting_dt and posting_dt >= cur_dt):
				agg[key]["warehouse_location"] = slot
				agg[key]["_location_date"] = posting_dt
	for key in agg:
		agg[key].pop("_location_date", None)
	return agg


def _get_outsourced_by_key(list_type=LIST_TYPE_BLANK):
	"""
	委外数/委外明细。
	- blank: Material Transfer 毛坯仓→半成品仓，key 的 warehouse = 毛坯仓。
	- semi_finished: Material Transfer 半成品仓→成品仓，key 的 warehouse = 半成品仓。
	返回: dict key (project_no, item_code, warehouse) -> { "outsourcing_qty", "outsourcing_details", "supplier_id", "supplier_name" }
	"""
	se_meta = frappe.get_meta("Stock Entry")
	has_customer_order = bool(se_meta.get_field("custom_customer_order"))
	has_outsourcing_supplier = bool(se_meta.get_field("custom_outsourcing_supplier"))

	select = [
		"sed.s_warehouse AS warehouse",
		"sed.item_code",
		"sed.qty",
		"se.posting_date",
		"se.name AS se_name",
	]
	if has_customer_order:
		select.append("se.custom_customer_order AS project_no")
	else:
		select.append("'' AS project_no")
	if has_outsourcing_supplier:
		select.append("se.custom_outsourcing_supplier AS supplier_id")
	else:
		select.append("NULL AS supplier_id")

	if list_type == LIST_TYPE_SEMI_FINISHED:
		s_wh, t_wh = SEMI_FINISHED_WAREHOUSE, FINISHED_WAREHOUSE
		default_wh = SEMI_FINISHED_WAREHOUSE
	else:
		s_wh, t_wh = BLANK_WAREHOUSE, SEMI_FINISHED_WAREHOUSE
		default_wh = BLANK_WAREHOUSE

	sql = """
		SELECT {}
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND {}
		WHERE se.purpose = 'Material Transfer'
		  AND sed.s_warehouse = %s AND sed.t_warehouse = %s
	""".format(", ".join(select), _STE_DOCSTATUS_FOR_LIST_SQL)
	rows = frappe.db.sql(sql, (s_wh, t_wh), as_dict=True)

	agg = {}
	for r in rows:
		project_no = (r.get("project_no") or "").strip()
		warehouse = r.get("warehouse") or default_wh
		key = (project_no, r.get("item_code"), warehouse)
		if key not in agg:
			agg[key] = {
				"outsourcing_qty": 0,
				"outsourcing_details": [],
				"supplier_id": None,
				"supplier_name": None,
			}
		qty = _flt(r.get("qty"))
		agg[key]["outsourcing_qty"] += qty
		agg[key]["outsourcing_details"].append({
			"date": _date_str(r.get("posting_date")),
			"docNo": r.get("se_name"),
			"qty": qty,
		})
		if has_outsourcing_supplier and r.get("supplier_id"):
			agg[key]["supplier_id"] = r.get("supplier_id")
			if not agg[key]["supplier_name"]:
				agg[key]["supplier_name"] = frappe.db.get_value("Supplier", r.get("supplier_id"), "supplier_name") or r.get("supplier_id")

	for key, v in agg.items():
		if v["supplier_name"] is None and v["supplier_id"]:
			v["supplier_name"] = frappe.db.get_value("Supplier", v["supplier_id"], "supplier_name") or v["supplier_id"]

	return agg


def _get_order_qty_by_so_item():
	"""
	订单数：Sales Order Item 按 (sales_order, item_code) 汇总 qty。
	同时返回按销售订单汇总的 total 映射，用于「毛坯行 item 与 SO 行 item 不一致」时回退显示该 SO 的整单数量。
	返回: (order_qty_map, order_qty_by_so)
	  - order_qty_map: (sales_order, item_code) -> qty
	  - order_qty_by_so: sales_order -> 该 SO 下所有行 qty 之和
	"""
	sql = """
		SELECT so_item.parent AS sales_order, so_item.item_code,
		       SUM(IFNULL(so_item.stock_qty, so_item.qty)) AS order_qty
		FROM `tabSales Order Item` so_item
		INNER JOIN `tabSales Order` so ON so.name = so_item.parent AND so.docstatus = 1
		GROUP BY so_item.parent, so_item.item_code
	"""
	rows = frappe.db.sql(sql, as_dict=True)
	order_qty_map = {(r.get("sales_order"), r.get("item_code")): _flt(r.get("order_qty")) for r in rows}
	# 按 SO 汇总整单数量（毛坯行物料可能与 SO 行成品物料不同，用整单数回退）
	order_qty_by_so = {}
	for r in rows:
		so_name = r.get("sales_order")
		if so_name:
			order_qty_by_so[so_name] = order_qty_by_so.get(so_name, 0) + _flt(r.get("order_qty"))
	return order_qty_map, order_qty_by_so


def _sanitize_order_by(order_by):
	"""只保留允许的列名，避免 SQL 注入。"""
	parts = []
	for part in order_by.split(","):
		part = part.strip()
		if not part:
			continue
		asc_desc = ""
		if " asc" in part.lower():
			part, asc_desc = part.rsplit(None, 1)
			asc_desc = " ASC"
		elif " desc" in part.lower():
			part, asc_desc = part.rsplit(None, 1)
			asc_desc = " DESC"
		col = part.strip().lower()
		if col in _ORDER_COLUMNS:
			parts.append(part + asc_desc)
	if not parts:
		return "project_no asc, item_code asc"
	return ", ".join(parts)


def _get_item_warehouse_slot(item_code):
	"""物料主数据上的库位（Item 若有 warehouse_slot 自定义字段则返回）。"""
	if not item_code:
		return ""
	item_meta = frappe.get_meta("Item")
	if not item_meta.get_field("warehouse_slot"):
		return ""
	val = frappe.db.get_value("Item", item_code, "warehouse_slot")
	return (val or "").strip()


def _build_row(key, received_data, outsourced_data, order_qty_map, order_qty_by_so, status):
	"""
	组装前端行对象，字段名驼峰、类型与前端约定一致。
	key = (project_no, item_code, warehouse)。
	order_qty：先用 (project_no, item_code) 查；若无（如毛坯与 SO 行成品物料不同）则用该销售订单整单数量。
	库位：优先用到库来源的 warehouse_location（毛坯来自 PR 行 warehouse_slot），否则回退到 Item.warehouse_slot。
	"""
	project_no, item_code, warehouse = key
	received_qty = _flt(received_data.get("received_qty", 0))
	receipt_details = received_data.get("receipt_details") or []
	outsourcing_qty = _flt(outsourced_data.get("outsourcing_qty", 0))
	outsourcing_details = outsourced_data.get("outsourcing_details") or []
	outsourcing_remainder = max(0.0, received_qty - outsourcing_qty)

	order_qty = _flt(order_qty_map.get((project_no, item_code)))
	if order_qty == 0 and project_no:
		order_qty = _flt(order_qty_by_so.get(project_no, 0))
	unreceived_qty = max(0.0, order_qty - received_qty)

	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else ""
	item_name = (item_name or "").strip()

	# 库位：到库聚合中的库位 > Item.warehouse_slot
	warehouse_location = (received_data.get("warehouse_location") or "").strip()
	if not warehouse_location:
		warehouse_location = _get_item_warehouse_slot(item_code)

	row_id = "|".join([str(project_no or ""), str(item_code or ""), str(warehouse or "")])
	supplier_id = (outsourced_data.get("supplier_id") or "").strip()
	supplier_name = (outsourced_data.get("supplier_name") or "").strip()

	# 确保数值为 number 类型（避免前端拿到字符串 "0"）；字符串无值时统一为 ""
	return {
		"id": row_id,
		"projectNo": project_no or "",
		"itemCode": item_code or "",
		"itemFullName": item_name or "",
		"orderQty": order_qty,
		"receivedQty": received_qty,
		"receiptDetails": list(receipt_details),
		"unreceivedQty": unreceived_qty,
		"warehouse": warehouse or BLANK_WAREHOUSE,
		"warehouseLocation": warehouse_location or "",
		"outsourcingSupplierId": supplier_id or "",
		"outsourcingSupplier": supplier_name or "",
		"outsourcingQty": outsourcing_qty,
		"outsourcingDetails": list(outsourcing_details),
		"outsourcingRemainder": outsourcing_remainder,
		"packingQty": None,
		"boxConfig": None,
		"volume": None,
		"unitPrice": None,
		"totalAmount": None,
		"workInstructionUrl": None,
		"status": status,
	}


@frappe.whitelist()
def get_pending_outsourcing_list(**kwargs):
	"""
	待委外列表（毛坯/半成品）：行粒度 销售订单号+物料+仓库，仅返回 到库数>0 且 委外余数>0 的行。
	到库/委外数量含调拨单草稿与已提交（不含已取消）。
	POST json_data: limit_start, limit_page_length, order_by, search_project_no, search_outsourcing_supplier, list_type
	  - list_type: "blank"（默认）毛坯；"semi_finished" 半成品。
	返回: message = [ 行对象 ], total_count
	"""
	params = _parse_params(kwargs)
	limit_start = max(0, int(params["limit_start"]))
	try:
		limit_page_length = int(params["limit_page_length"])
	except (TypeError, ValueError):
		limit_page_length = 50
	use_limit = limit_page_length > 0
	order_by = _sanitize_order_by(params["order_by"])
	search_project = (params.get("search_project_no") or "").strip()
	search_supplier = (params.get("search_outsourcing_supplier") or "").strip()
	list_type = params.get("list_type") or LIST_TYPE_BLANK

	received = _get_received_by_key(list_type)
	outsourced = _get_outsourced_by_key(list_type)
	order_qty_map, order_qty_by_so = _get_order_qty_by_so_item()

	# 待委外：到库数>0 且 委外余数>0（即 received_qty > outsourcing_qty）
	candidates = []
	for key, rec in received.items():
		received_qty = rec["received_qty"]
		if received_qty <= 0:
			continue
		outs = outsourced.get(key, {})
		outs_qty = outs.get("outsourcing_qty", 0)
		if received_qty <= outs_qty:
			continue
		project_no, item_code, warehouse = key
		if search_project and search_project.lower() not in (project_no or "").lower():
			continue
		if search_supplier:
			supp = (outs.get("supplier_name") or "") + (outs.get("supplier_id") or "")
			if search_supplier.lower() not in supp.lower():
				continue
		candidates.append((key, rec, outs))

	# 排序：按 order_by 解析的列排序（内存排序，因 key 为元组）
	def sort_key(item):
		key = item[0]
		project_no, item_code, warehouse = key
		return (project_no or "", item_code or "", warehouse or "")

	candidates.sort(key=sort_key)
	total_count = len(candidates)

	# 分页
	if use_limit:
		page = candidates[limit_start: limit_start + limit_page_length]
	else:
		page = candidates[limit_start:]

	out = []
	for key, rec, outs in page:
		row = _build_row(key, rec, outs, order_qty_map, order_qty_by_so, "待委外")
		out.append(row)

	frappe.response["message"] = out
	frappe.response["total_count"] = total_count


@frappe.whitelist()
def get_outsourced_list(**kwargs):
	"""
	已委外列表（毛坯/半成品）：行粒度 销售订单号+物料+仓库，仅返回 委外数>0 的行；同一套行结构，status=已委外。
	POST json_data: 同 get_pending_outsourcing_list（含 list_type）；排序建议按委外日期倒序。
	返回: message = [ 行对象 ], total_count
	"""
	params = _parse_params(kwargs)
	limit_start = max(0, int(params["limit_start"]))
	try:
		limit_page_length = int(params["limit_page_length"])
	except (TypeError, ValueError):
		limit_page_length = 50
	use_limit = limit_page_length > 0
	order_by = _sanitize_order_by(params.get("order_by") or "posting_date desc, item_code asc")
	search_project = (params.get("search_project_no") or "").strip()
	search_supplier = (params.get("search_outsourcing_supplier") or "").strip()
	list_type = params.get("list_type") or LIST_TYPE_BLANK

	received = _get_received_by_key(list_type)
	outsourced = _get_outsourced_by_key(list_type)
	order_qty_map, order_qty_by_so = _get_order_qty_by_so_item()

	# 已委外：委外数>0
	candidates = []
	for key, outs in outsourced.items():
		outs_qty = outs.get("outsourcing_qty", 0)
		if outs_qty <= 0:
			continue
		project_no, item_code, warehouse = key
		if search_project and search_project.lower() not in (project_no or "").lower():
			continue
		if search_supplier:
			supp = (outs.get("supplier_name") or "") + (outs.get("supplier_id") or "")
			if search_supplier.lower() not in supp.lower():
				continue
		rec = received.get(key, {"received_qty": 0, "receipt_details": []})
		candidates.append((key, rec, outs))

	def sort_key(item):
		key = item[0]
		project_no, item_code, warehouse = key
		return (project_no or "", item_code or "", warehouse or "")

	candidates.sort(key=sort_key)
	total_count = len(candidates)

	if use_limit:
		page = candidates[limit_start: limit_start + limit_page_length]
	else:
		page = candidates[limit_start:]

	out = []
	for key, rec, outs in page:
		row = _build_row(key, rec, outs, order_qty_map, order_qty_by_so, "已委外")
		out.append(row)

	frappe.response["message"] = out
	frappe.response["total_count"] = total_count
