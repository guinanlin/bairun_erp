# Copyright (c) 2026, Bairun and contributors
# 入库质检列表 / 单行详情：与采购未交列表中 pending_qc、latest_quality_inspection 判定一致
# （reference_type=Purchase Receipt + reference_name + child_row_reference=PR 子表 name，且 QI docstatus=1）。
#
# 列表: get_inbound_qc_list
# 详情: get_inbound_qc_line_detail
# 请求: POST，json_data 与项目惯例一致

from __future__ import unicode_literals

import json
from collections import defaultdict

import frappe
from frappe.utils import flt, get_datetime_str

_MAX_PAGE = 100
_DEFAULT_PAGE = 20

# order_by 中允许的「逻辑列名」→ SQL 表达式（防注入）
_ORDER_BY_MAP = {
	"posting_date": "pr.posting_date",
	"purchase_receipt": "pr.name",
	"idx": "pri.idx",
	"creation": "pr.creation",
}


def _parse_inbound_params(kwargs):
	params = {
		"limit_start": 0,
		"limit_page_length": _DEFAULT_PAGE,
		"order_by": None,
		"qc_line_status": "all",
		"search_purchase_receipt": None,
		"search_supplier": None,
		"search_item": None,
		"search_purchase_order": None,
		"search_sales_order": None,
		"from_posting_date": None,
		"to_posting_date": None,
		"qi_status": None,
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

	for k in list(params.keys()):
		if k in jd and jd.get(k) is not None:
			params[k] = jd.get(k)
	return params


def _clamp_limit(n):
	try:
		n = int(n)
	except (TypeError, ValueError):
		return _DEFAULT_PAGE
	if n <= 0:
		return _DEFAULT_PAGE
	return min(n, _MAX_PAGE)


def _build_order_sql(order_by):
	default = "pr.posting_date DESC, pr.name DESC, pri.idx ASC"
	if not order_by or not str(order_by).strip():
		return default
	parts = []
	for seg in str(order_by).split(","):
		seg = seg.strip()
		if not seg:
			continue
		tokens = seg.split()
		col = (tokens[0] or "").lower()
		direction = (tokens[1] if len(tokens) > 1 else "asc").upper()
		if direction not in ("ASC", "DESC"):
			direction = "ASC"
		sql_col = _ORDER_BY_MAP.get(col)
		if sql_col:
			parts.append("{} {}".format(sql_col, direction))
	return ", ".join(parts) if parts else default


def _qi_fieldnames_for_select():
	"""QI 列表查询字段：标准列 + 百润自定义列（存在则 SELECT）。"""
	base = [
		"reference_name",
		"child_row_reference",
		"name",
		"status",
		"creation",
		"modified",
		"sample_size",
		"inspected_by",
	]
	meta = frappe.get_meta("Quality Inspection")
	for fn in ("custom_good_qty", "custom_defective_qty", "custom_defective_handling", "custom_order_qty"):
		if meta.get_field(fn):
			base.append(fn)
	# 去重保序
	seen = set()
	out = []
	for f in base:
		if f not in seen:
			seen.add(f)
			out.append(f)
	return out


def _batch_qi_summary_for_pairs(pairs):
	"""
	pairs: [(purchase_receipt, pr_item_name), ...]
	返回: (pr, pri) -> { latest_qi: dict|None, quality_inspection_count: int }
	同一 PR 行多笔已提交 QI 时，latest_qi 取 creation 最大的一条（与采购未交 enrich 一致）。
	"""
	if not pairs:
		return {}
	placeholders = ", ".join(["(%s,%s)"] * len(pairs))
	flat = [x for t in pairs for x in t]
	fields = ", ".join(_qi_fieldnames_for_select())
	sql = """
		SELECT {fields}
		FROM `tabQuality Inspection`
		WHERE reference_type = 'Purchase Receipt'
		AND docstatus = 1
		AND (reference_name, child_row_reference) IN ({ph})
	""".format(fields=fields, ph=placeholders)
	rows = frappe.db.sql(sql, flat, as_dict=True)
	by_pair = defaultdict(list)
	for r in rows:
		key = (r.get("reference_name"), r.get("child_row_reference"))
		by_pair[key].append(r)

	out = {}
	for key in pairs:
		lst = by_pair.get(key) or []
		cnt = len(lst)
		latest = None
		for q in lst:
			if not latest:
				latest = q
				continue
			qc, lc = q.get("creation"), latest.get("creation")
			if qc and (not lc or qc > lc):
				latest = q
		out[key] = {"latest_qi": latest, "quality_inspection_count": cnt}
	return out


def _latest_stock_entry_name(purchase_receipt, item_code):
	"""与 submit 写入的 reference_purchase_receipt + 行物料一致；多行同料时取最近一张入库单。"""
	if not purchase_receipt or not item_code:
		return None
	r = frappe.db.sql(
		"""
		SELECT se.name
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.docstatus = 1 AND se.purpose = 'Material Receipt'
		AND sed.reference_purchase_receipt = %s AND sed.item_code = %s
		ORDER BY se.creation DESC
		LIMIT 1
		""",
		(purchase_receipt, item_code),
		as_dict=True,
	)
	return r[0]["name"] if r else None


def _append_qc_sql_filters(conditions, values, qc_line_status, qi_status):
	"""
	qc_line_status: all | pending | done
	qi_status: Accepted | Rejected | None — 仅对「最新一笔 QI」结论筛选（与需求文档一致）。
	"""
	st = (qc_line_status or "all").strip().lower()
	qs = (qi_status or "").strip()
	if qs not in ("Accepted", "Rejected"):
		qs = None

	base_exists = """
EXISTS (
	SELECT 1 FROM `tabQuality Inspection` qix
	WHERE qix.reference_type = 'Purchase Receipt'
	AND qix.reference_name = pri.parent
	AND qix.child_row_reference = pri.name
	AND qix.docstatus = 1
)
"""
	base_not_exists = """
NOT EXISTS (
	SELECT 1 FROM `tabQuality Inspection` qix
	WHERE qix.reference_type = 'Purchase Receipt'
	AND qix.reference_name = pri.parent
	AND qix.child_row_reference = pri.name
	AND qix.docstatus = 1
)
"""
	latest_status_match = """
EXISTS (
	SELECT 1 FROM `tabQuality Inspection` qil
	WHERE qil.reference_type = 'Purchase Receipt'
	AND qil.reference_name = pri.parent
	AND qil.child_row_reference = pri.name
	AND qil.docstatus = 1
	AND qil.status = %s
	AND qil.creation = (
		SELECT MAX(qim.creation) FROM `tabQuality Inspection` qim
		WHERE qim.reference_type = 'Purchase Receipt'
		AND qim.reference_name = pri.parent
		AND qim.child_row_reference = pri.name
		AND qim.docstatus = 1
	)
)
"""

	if st == "pending":
		conditions.append(base_not_exists)
	elif st == "done":
		if qs:
			conditions.append(latest_status_match)
			values.append(qs)
		else:
			conditions.append(base_exists)
	else:
		# all
		if qs:
			conditions.append(latest_status_match)
			values.append(qs)


def _sales_order_sql_expr(has_po_customer_order):
	if has_po_customer_order:
		return "COALESCE(po_so.customer_order, poi_so.sales_order)"
	return "poi_so.sales_order"


@frappe.whitelist()
def get_inbound_qc_list(**kwargs):
	"""
	入库质检列表：已提交采购接收（PR）子表行维度，待检/已检与 get_purchase_order_unfulfilled_list 中 QI 判定一致。

	POST json_data:
	  limit_start, limit_page_length (默认 20，最大 100),
	  order_by: 如 posting_date desc, purchase_receipt desc, idx asc（仅允许 posting_date/purchase_receipt/idx/creation）
	  qc_line_status: all | pending | done
	  search_purchase_receipt, search_supplier, search_item, search_purchase_order, search_sales_order,
	  from_posting_date, to_posting_date (YYYY-MM-DD),
	  qi_status: Accepted | Rejected（在 qc_line_status 为 all/done 时按「最新 QI」筛选）

	响应: frappe.response.message = { "items": [...], "total_count": N }
	"""
	params = _parse_inbound_params(kwargs)
	limit_start = int(params.get("limit_start") or 0)
	if limit_start < 0:
		limit_start = 0
	limit_page_length = _clamp_limit(params.get("limit_page_length"))

	po_meta = frappe.get_meta("Purchase Order")
	has_po_customer_order = bool(po_meta.get_field("customer_order"))
	sales_expr = _sales_order_sql_expr(has_po_customer_order)

	pri_meta = frappe.get_meta("Purchase Receipt Item")
	has_batch_no = bool(pri_meta.get_field("batch_no"))

	select_parts = [
		"pri.parent AS purchase_receipt",
		"pri.name AS pr_item_name",
		"pri.idx AS idx",
		"pr.posting_date AS posting_date",
		"pri.item_code AS item_code",
		"pri.item_name AS item_name",
		"pri.qty AS qty",
		"pri.uom AS uom",
		"pri.warehouse AS warehouse",
		"pri.purchase_order AS purchase_order",
		"pri.purchase_order_item AS purchase_order_item",
		"pr.supplier AS supplier",
		"pr.supplier_name AS supplier_name",
		"{} AS sales_order".format(sales_expr),
	]
	if has_batch_no:
		select_parts.append("pri.batch_no AS batch_no")

	conditions = []
	values = []

	sp = (params.get("search_purchase_receipt") or "").strip()
	if sp:
		conditions.append("pr.name LIKE %s")
		values.append("%" + sp + "%")

	ss = (params.get("search_supplier") or "").strip()
	if ss:
		conditions.append("(pr.supplier LIKE %s OR pr.supplier_name LIKE %s)")
		values.extend(["%" + ss + "%", "%" + ss + "%"])

	si = (params.get("search_item") or "").strip()
	if si:
		conditions.append("(pri.item_code LIKE %s OR pri.item_name LIKE %s)")
		values.extend(["%" + si + "%", "%" + si + "%"])

	spo = (params.get("search_purchase_order") or "").strip()
	if spo:
		conditions.append("IFNULL(pri.purchase_order, '') LIKE %s")
		values.append("%" + spo + "%")

	sso = (params.get("search_sales_order") or "").strip()
	if sso:
		conditions.append("IFNULL(({}), '') LIKE %s".format(sales_expr))
		values.append("%" + sso + "%")

	fd = (params.get("from_posting_date") or "").strip()
	if fd:
		conditions.append("pr.posting_date >= %s")
		values.append(fd)
	td = (params.get("to_posting_date") or "").strip()
	if td:
		conditions.append("pr.posting_date <= %s")
		values.append(td)

	_append_qc_sql_filters(
		conditions,
		values,
		params.get("qc_line_status"),
		params.get("qi_status"),
	)

	where_extra = ""
	if conditions:
		where_extra = " AND " + " AND ".join(conditions)

	from_sql = """
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent AND pr.docstatus = 1
		LEFT JOIN `tabPurchase Order` po_so ON po_so.name = pri.purchase_order
		LEFT JOIN `tabPurchase Order Item` poi_so ON poi_so.name = pri.purchase_order_item
		WHERE 1=1
	"""

	order_sql = _build_order_sql(params.get("order_by"))

	count_sql = "SELECT COUNT(*) AS cnt " + from_sql + where_extra
	count_row = frappe.db.sql(count_sql, values, as_dict=True)
	total_count = int(count_row[0]["cnt"]) if count_row else 0

	data_sql = "SELECT " + ", ".join(select_parts) + from_sql + where_extra + " ORDER BY " + order_sql
	data_sql += " LIMIT %s, %s"
	run_values = list(values) + [limit_start, limit_page_length]
	rows = frappe.db.sql(data_sql, run_values, as_dict=True)

	pairs = [(r.get("purchase_receipt"), r.get("pr_item_name")) for r in rows]
	summ = _batch_qi_summary_for_pairs(pairs)

	items = []
	for r in rows:
		key = (r.get("purchase_receipt"), r.get("pr_item_name"))
		s = summ.get(key) or {"latest_qi": None, "quality_inspection_count": 0}
		latest = s["latest_qi"]
		qc_line_status = "done" if latest else "pending"

		item = {
			"purchase_receipt": r.get("purchase_receipt"),
			"pr_item_name": r.get("pr_item_name"),
			"idx": r.get("idx"),
			"posting_date": str(r.get("posting_date")) if r.get("posting_date") is not None else None,
			"item_code": r.get("item_code"),
			"item_name": r.get("item_name"),
			"qty": flt(r.get("qty")),
			"uom": r.get("uom"),
			"warehouse": r.get("warehouse"),
			"purchase_order": r.get("purchase_order"),
			"purchase_order_item": r.get("purchase_order_item"),
			"sales_order": r.get("sales_order"),
			"supplier": r.get("supplier"),
			"supplier_name": r.get("supplier_name"),
			"qc_line_status": qc_line_status,
			"row_key": "{}::{}".format(r.get("purchase_receipt") or "", r.get("pr_item_name") or ""),
			"quality_inspection_count": int(s["quality_inspection_count"]),
		}
		if has_batch_no:
			item["batch_no"] = r.get("batch_no")

		if latest:
			good = flt(latest.get("custom_good_qty")) if latest.get("custom_good_qty") is not None else None
			bad = flt(latest.get("custom_defective_qty")) if latest.get("custom_defective_qty") is not None else None
			inspected = None
			if good is not None or bad is not None:
				inspected = flt((good or 0) + (bad or 0))
			if inspected is None or inspected == 0:
				inspected = flt(latest.get("sample_size"))

			item.update(
				{
					"quality_inspection": latest.get("name"),
					"qi_status": latest.get("status"),
					"qi_inspected_qty": inspected,
					"qi_good_qty": good,
					"qi_defective_qty": bad,
					"qi_defective_handling": (latest.get("custom_defective_handling") or "").strip() or None,
					"qi_modified": get_datetime_str(latest.get("modified")) if latest.get("modified") else None,
					"qi_inspector": latest.get("inspected_by"),
					"stock_entry": _latest_stock_entry_name(r.get("purchase_receipt"), r.get("item_code")),
				}
			)
		else:
			item.update(
				{
					"quality_inspection": None,
					"qi_status": None,
					"qi_inspected_qty": None,
					"qi_good_qty": None,
					"qi_defective_qty": None,
					"qi_defective_handling": None,
					"qi_modified": None,
					"qi_inspector": None,
					"stock_entry": None,
				}
			)

		items.append(item)

	frappe.response["message"] = {"items": items, "total_count": total_count}
	return


@frappe.whitelist()
def get_inbound_qc_line_detail(**kwargs):
	"""
	单行详情：purchase_receipt + pr_item_name 必填。
	返回 PR 行基础信息、全部已提交质检单（按 creation 倒序）、关联入库单号列表。
	"""
	jd = kwargs.get("json_data")
	if jd is None:
		jd = kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			jd = {}
	if not isinstance(jd, dict):
		jd = {}

	pr_name = (jd.get("purchase_receipt") or kwargs.get("purchase_receipt") or "").strip()
	pri_name = (jd.get("pr_item_name") or kwargs.get("pr_item_name") or "").strip()
	if not pr_name or not pri_name:
		frappe.throw(frappe._("purchase_receipt 与 pr_item_name 必填"))

	if not frappe.db.exists("Purchase Receipt", pr_name):
		frappe.throw(frappe._("采购接收单不存在"))

	pr = frappe.get_doc("Purchase Receipt", pr_name)
	if pr.docstatus != 1:
		frappe.throw(frappe._("采购接收单未提交"))

	row = None
	for d in pr.items or []:
		if d.name == pri_name:
			row = d
			break
	if not row:
		frappe.throw(frappe._("采购接收单行不存在"))

	po_so = row.purchase_order
	poi_so = frappe.db.get_value(
		"Purchase Order Item",
		row.purchase_order_item,
		["sales_order"],
		as_dict=True,
	) if row.purchase_order_item else None
	sales_order = None
	if po_so:
		po_meta = frappe.get_meta("Purchase Order")
		if po_meta.get_field("customer_order"):
			sales_order = frappe.db.get_value("Purchase Order", po_so, "customer_order")
		if not sales_order and poi_so:
			sales_order = poi_so.get("sales_order")

	base = {
		"purchase_receipt": pr_name,
		"pr_item_name": pri_name,
		"idx": row.idx,
		"posting_date": str(pr.posting_date) if pr.posting_date else None,
		"item_code": row.item_code,
		"item_name": row.item_name,
		"qty": flt(row.qty),
		"uom": row.uom,
		"warehouse": row.warehouse,
		"purchase_order": row.purchase_order,
		"purchase_order_item": row.purchase_order_item,
		"sales_order": sales_order,
		"supplier": pr.supplier,
		"supplier_name": pr.supplier_name,
		"row_key": "{}::{}".format(pr_name, pri_name),
	}
	if frappe.get_meta("Purchase Receipt Item").get_field("batch_no"):
		base["batch_no"] = getattr(row, "batch_no", None)

	fields = ", ".join(_qi_fieldnames_for_select())
	qi_rows = frappe.db.sql(
		"""
		SELECT {fields}
		FROM `tabQuality Inspection`
		WHERE reference_type = 'Purchase Receipt'
		AND docstatus = 1
		AND reference_name = %s AND child_row_reference = %s
		ORDER BY creation DESC
		""".format(fields=fields),
		(pr_name, pri_name),
		as_dict=True,
	)

	quality_inspection_list = []
	for q in qi_rows:
		good = flt(q.get("custom_good_qty")) if q.get("custom_good_qty") is not None else None
		bad = flt(q.get("custom_defective_qty")) if q.get("custom_defective_qty") is not None else None
		inspected = None
		if good is not None or bad is not None:
			inspected = flt((good or 0) + (bad or 0))
		if inspected is None or inspected == 0:
			inspected = flt(q.get("sample_size"))
		quality_inspection_list.append(
			{
				"name": q.get("name"),
				"status": q.get("status"),
				"creation": get_datetime_str(q.get("creation")) if q.get("creation") else None,
				"modified": get_datetime_str(q.get("modified")) if q.get("modified") else None,
				"sample_size": flt(q.get("sample_size")),
				"qi_inspected_qty": inspected,
				"qi_good_qty": good,
				"qi_defective_qty": bad,
				"qi_defective_handling": (q.get("custom_defective_handling") or "").strip() or None,
				"qi_inspector": q.get("inspected_by"),
			}
		)

	latest = quality_inspection_list[0] if quality_inspection_list else None
	base["qc_line_status"] = "done" if latest else "pending"
	base["quality_inspection_count"] = len(quality_inspection_list)

	se_rows = frappe.db.sql(
		"""
		SELECT se.name, MAX(se.creation) AS _mc
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.docstatus = 1 AND se.purpose = 'Material Receipt'
		AND sed.reference_purchase_receipt = %s AND sed.item_code = %s
		GROUP BY se.name
		ORDER BY _mc DESC
		""",
		(pr_name, row.item_code),
		as_dict=True,
	)
	stock_entries = [r["name"] for r in se_rows]

	out = {
		**base,
		"quality_inspection_list": quality_inspection_list,
		"stock_entries": stock_entries,
	}
	if latest:
		out["quality_inspection"] = latest.get("name")
		out["qi_status"] = latest.get("status")
		out["stock_entry"] = stock_entries[0] if stock_entries else None
	else:
		out["quality_inspection"] = None
		out["qi_status"] = None
		out["stock_entry"] = None

	frappe.response["message"] = out
	return
