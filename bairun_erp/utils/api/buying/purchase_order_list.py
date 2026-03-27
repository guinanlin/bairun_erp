# Copyright (c) 2025, Bairun and contributors
# 采购订单列表接口：专用 API 返回采购订单列表，结构为 header（主表）+ lines（子表明细）。
#
# 采购订单列表: get_purchase_order_list
# 采购未交列表: get_purchase_order_unfulfilled_list
# 请求方式: POST，Content-Type: application/json

from __future__ import unicode_literals

import json
from collections import defaultdict

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


def _flt(val, default=0):
	try:
		return float(val) if val is not None else default
	except (TypeError, ValueError):
		return default


def _enrich_unfulfilled_rows_qc_pr(rows):
	"""
	为未交行批量补充 can_create_pr、pending_qc、latest_quality_inspection，避免前端 N+1。

	can_create_pr：按 ERP 采购订单行「可再收数量」(qty - po_received_qty) 判断；PR 已收满但尚未最终入库时
	业务上仍可能出现在未交列表，但不应再引导创建新 PR。
	pending_qc：已提交 PR、且该行尚无已提交质检单时，返回最早一条待检 PR 行（多 PR 时只取一个）。
	latest_quality_inspection：与该 PO 行关联的已提交质检单中，按创建时间最新的一条单号。
	"""
	if not rows:
		return

	po_names = list({r.get("purchase_order") for r in rows if r.get("purchase_order")})
	if not po_names:
		for r in rows:
			qty = _flt(r.get("qty"))
			po_recv = _flt(r.get("po_received_qty"))
			r["can_create_pr"] = (qty - po_recv) > 0
			r["pending_qc"] = None
			r["latest_quality_inspection"] = None
		return

	# 已提交 PR 下、关联到这些采购单的收货行（按 PR 过账日期、创建时间、行序 早优先）
	pr_items = frappe.db.sql(
		"""
		SELECT pri.parent AS purchase_receipt, pri.name AS pr_item_name,
			pri.purchase_order, pri.purchase_order_item, pri.item_code,
			pr.posting_date, pr.creation
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent AND pr.docstatus = 1
		WHERE pri.purchase_order IN %s
		ORDER BY pr.posting_date ASC, pr.creation ASC, pri.idx ASC
		""",
		[po_names],
		as_dict=True,
	)

	# (PO, PO Item name) -> 收货行列表
	by_po_item = defaultdict(list)
	# (PO, item_code) -> 收货行（仅 purchase_order_item 为空时用于兜底匹配）
	by_po_itemcode = defaultdict(list)
	for pri in pr_items:
		po = pri.get("purchase_order")
		poi = (pri.get("purchase_order_item") or "").strip()
		if poi:
			by_po_item[(po, poi)].append(pri)
		else:
			ic = pri.get("item_code")
			if ic:
				by_po_itemcode[(po, ic)].append(pri)

	pairs = list(dict.fromkeys((p["purchase_receipt"], p["pr_item_name"]) for p in pr_items))
	qi_by_pair = {}
	if pairs:
		# 已提交质检单：(PR 单号, PR 行 name) -> { name, creation }
		placeholders = ", ".join(["(%s,%s)"] * len(pairs))
		flat = [x for t in pairs for x in t]
		qis = frappe.db.sql(
			"""
			SELECT reference_name, child_row_reference, name, creation
			FROM `tabQuality Inspection`
			WHERE reference_type = 'Purchase Receipt'
			AND docstatus = 1
			AND (reference_name, child_row_reference) IN ({})
			""".format(placeholders),
			flat,
			as_dict=True,
		)
		for q in qis:
			key = (q.get("reference_name"), q.get("child_row_reference"))
			prev = qi_by_pair.get(key)
			if not prev:
				qi_by_pair[key] = q
				continue
			qc, pc = q.get("creation"), prev.get("creation")
			if qc and (not pc or qc > pc):
				qi_by_pair[key] = q

	# 该采购单下、与 PO 行可能关联的已提交质检（用于 latest）
	qi_rows_for_po = frappe.db.sql(
		"""
		SELECT pri.purchase_order, pri.purchase_order_item, pri.item_code,
			qi.name AS qi_name, qi.creation
		FROM `tabQuality Inspection` qi
		INNER JOIN `tabPurchase Receipt Item` pri
			ON pri.parent = qi.reference_name AND pri.name = qi.child_row_reference
		WHERE qi.reference_type = 'Purchase Receipt'
		AND qi.docstatus = 1
		AND pri.purchase_order IN %s
		""",
		[po_names],
		as_dict=True,
	)

	def _latest_qi_for_line(po, po_item_name, item_code):
		best = None
		for rec in qi_rows_for_po:
			if rec.get("purchase_order") != po:
				continue
			poi = (rec.get("purchase_order_item") or "").strip()
			if poi:
				if poi != po_item_name:
					continue
			else:
				if rec.get("item_code") != item_code:
					continue
			if not best:
				best = rec
			else:
				rc, bc = rec.get("creation"), best.get("creation")
				if rc and (not bc or rc > bc):
					best = rec
		return best.get("qi_name") if best else None

	for r in rows:
		qty = _flt(r.get("qty"))
		po_recv = _flt(r.get("po_received_qty"))
		r["can_create_pr"] = (qty - po_recv) > 0

		po = r.get("purchase_order")
		po_item_name = r.get("po_item_name")
		item_code = r.get("item_code")

		candidates = []
		if po_item_name:
			candidates = list(by_po_item.get((po, po_item_name)) or [])
		if not candidates and item_code:
			candidates = list(by_po_itemcode.get((po, item_code)) or [])

		pending_qc = None
		for pri in candidates:
			key = (pri.get("purchase_receipt"), pri.get("pr_item_name"))
			if key not in qi_by_pair:
				pending_qc = {
					"purchase_receipt": pri.get("purchase_receipt"),
					"pr_item_name": pri.get("pr_item_name"),
				}
				break

		r["pending_qc"] = pending_qc
		r["latest_quality_inspection"] = _latest_qi_for_line(po, po_item_name, item_code)


@frappe.whitelist()
def get_purchase_order_unfulfilled_list(**kwargs):
	"""
	采购未交列表：返回未交数量 > 0 的采购订单行扁平列表。
	POST json_data: filters, order_by, limit_start, limit_page_length,
	  search_customer_order, search_supplier, search_item_name
	返回: { "message": [ 未交行对象, ... ], "total_count": 总条数 }

	未交口径（业务）：以「最终入库」为准——已提交且 purpose=Material Receipt 的 Stock Entry 明细数量
	（经 PR 行 purchase_order_item 关联到本 PO 行汇总），不满订单数量则仍为未交。
	PO/PR 的 received_qty 仅表示 ERP 收货回写，PR 收讫但未做质检入库时仍计为未交。

	未交行对象除原有字段外包含：
	- po_item_name: Purchase Order Item 的 name（用于与 PR 行、质检关联）
	- po_received_qty: PO 行上 ERP 已收货数量（received_qty），用于与「最终入库」区分
	- can_create_pr: 是否仍可按 ERP 再行收货（qty > po_received_qty）；PR 已满仅待质检/入库时为 false
	- pending_qc: 若有已提交 PR 行尚缺已提交质检单，则为 { purchase_receipt, pr_item_name }，否则 null（多笔待检时取最早一张 PR）
	- latest_quality_inspection: 与该 PO 行关联的已提交质检单号（最新一条），无则 null
	"""
	params = _parse_params(kwargs)
	# 默认按采购单创建日期倒序，便于查看最新创建的未交单
	order_by = (params.get("order_by") or "creation desc, purchase_order asc, idx asc").strip()
	# ORDER BY 中 creation 在 JOIN 下需限定为主表 po.creation，见下方 order_sql 处理
	limit_start = int(params.get("limit_start", 0))
	limit_page_length = params.get("limit_page_length", 50)
	try:
		limit_page_length = int(limit_page_length)
	except (TypeError, ValueError):
		limit_page_length = 50
	use_limit = limit_page_length and limit_page_length > 0
	if not use_limit:
		limit_page_length = None

	po_meta = frappe.get_meta("Purchase Order")
	item_meta = frappe.get_meta("Purchase Order Item")
	has_po_customer_order = bool(po_meta.get_field("customer_order"))

	select_parts = [
		"po.name as purchase_order",
		"item.name as po_item_name",
		"po.supplier as supplier",
		"po.supplier_name as supplier_name",
		"po.transaction_date as transaction_date",
		"item.schedule_date as schedule_date",
		"item.item_code as item_code",
		"item.item_name as item_name",
		"item.qty as qty",
		"IFNULL(item.received_qty, 0) as po_line_received_qty",
		"COALESCE(stocked_matched.sum_stocked, 0) AS stocked_qty",
		"item.rate as rate",
		"item.amount as amount",
		"item.warehouse as warehouse",
		"item.idx as idx",
	]
	if has_po_customer_order:
		select_parts.append("COALESCE(po.customer_order, item.sales_order) as customer_order")
	else:
		select_parts.append("item.sales_order as customer_order")
	for f in ("rework_qty", "order_confirmation_status", "warehouse_slot"):
		if item_meta.get_field(f):
			select_parts.append("item.{} as {}".format(f, f))

	conditions = [
		"po.status NOT IN ('Cancelled', 'Closed')",
		"(item.qty - COALESCE(stocked_matched.sum_stocked, 0)) > 0",
	]
	values = []

	search_co = (params.get("search_customer_order") or "").strip()
	search_sup = (params.get("search_supplier") or "").strip()
	search_item = (params.get("search_item_name") or "").strip()
	if search_co:
		if has_po_customer_order:
			conditions.append("(po.customer_order LIKE %s OR item.sales_order LIKE %s)")
			values.extend(["%" + search_co + "%", "%" + search_co + "%"])
		else:
			conditions.append("item.sales_order LIKE %s")
			values.append("%" + search_co + "%")
	if search_sup:
		conditions.append("(po.supplier LIKE %s OR po.supplier_name LIKE %s)")
		values.extend(["%" + search_sup + "%", "%" + search_sup + "%"])
	if search_item:
		conditions.append("(item.item_name LIKE %s OR item.item_code LIKE %s)")
		values.extend(["%" + search_item + "%", "%" + search_item + "%"])

	where_sql = " AND ".join(conditions)
	order_sql = order_by.replace("purchase_order", "po.name").replace("transaction_date", "po.transaction_date").replace("idx", "item.idx")
	# 避免 JOIN 下 ORDER BY 列歧义：creation 限定为主表
	if "creation" in order_sql.lower():
		order_sql = order_sql.replace("creation", "po.creation").replace("CREATION", "po.creation")

	# 最终入库量：已提交入库单（Material Receipt）明细 qty，按 PR 子表 purchase_order_item 归属到 PO 行
	_stock_join_sql = """
		LEFT JOIN (
			SELECT pri.purchase_order_item AS poi_key, SUM(sed.qty) AS sum_stocked
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent
				AND se.docstatus = 1
				AND se.purpose = 'Material Receipt'
			INNER JOIN `tabPurchase Receipt Item` pri ON pri.parent = sed.reference_purchase_receipt
			WHERE IFNULL(pri.purchase_order_item, '') != ''
			GROUP BY pri.purchase_order_item
		) stocked_matched ON stocked_matched.poi_key = item.name
	"""

	base_sql = """
		SELECT {}
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` item ON item.parent = po.name
		{}
		WHERE {}
	""".format(", ".join(select_parts), _stock_join_sql, where_sql)

	_stock_join_for_count = " ".join(_stock_join_sql.split())
	count_sql = (
		"SELECT COUNT(*) AS cnt FROM (`tabPurchase Order` po "
		"INNER JOIN `tabPurchase Order Item` item ON item.parent = po.name "
		+ _stock_join_for_count
		+ ") WHERE "
		+ where_sql
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
		qty = _flt(r.get("qty"))
		po_line_received = _flt(r.get("po_line_received_qty"))
		stocked_qty = _flt(r.get("stocked_qty"))
		outstanding_qty = qty - stocked_qty
		if outstanding_qty <= 0:
			continue
		rate = _flt(r.get("rate"))
		row = {
			"purchase_order": r.get("purchase_order"),
			"po_item_name": r.get("po_item_name"),
			"customer_order": r.get("customer_order"),
			"supplier": r.get("supplier"),
			"supplier_name": r.get("supplier_name"),
			"transaction_date": r.get("transaction_date"),
			"schedule_date": r.get("schedule_date"),
			"item_code": r.get("item_code"),
			"item_name": r.get("item_name"),
			"qty": qty,
			"po_received_qty": po_line_received,
			"received_qty": stocked_qty,
			"outstanding_qty": outstanding_qty,
			"rate": rate,
			"amount": _flt(r.get("amount")),
			"rework_qty": _flt(r.get("rework_qty")) if r.get("rework_qty") is not None else 0,
			"order_confirmation_status": r.get("order_confirmation_status"),
			"outstanding_amount": round(rate * outstanding_qty, 2),
			"warehouse": r.get("warehouse"),
			"warehouse_slot": r.get("warehouse_slot"),
			"rowKey": "{}-{}".format(r.get("purchase_order") or "", r.get("idx") or (len(out) + 1)),
		}
		out.append(row)

	_enrich_unfulfilled_rows_qc_pr(out)

	frappe.response["message"] = out
	frappe.response["total_count"] = total_count
	# 不返回 dict，避免 handler 将返回值赋给 response.message 覆盖掉 total_count
	return
