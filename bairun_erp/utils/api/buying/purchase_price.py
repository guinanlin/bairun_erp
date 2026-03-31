from __future__ import unicode_literals

import json

import frappe


# 与前端类目定义保持一致
ALLOWED_CATEGORIES = {
	"注塑",
	"UV镀",
	"罩光",
	"喷涂",
	"水镀",
	"滴油",
	"点钻",
	"组装",
	"手工活",
	"饰品配件",
	"烫金",
	"印刷",
	"玻璃瓶",
	"水转印",
	"热转印",
	"植绒",
}

FORM2_CATEGORIES = {"UV镀", "罩光", "喷涂", "水镀", "水转印", "热转印", "植绒"}
FORM3_CATEGORIES = {"滴油", "点钻", "组装", "手工活", "饰品配件", "烫金", "印刷"}
FORM4_CATEGORIES = {"玻璃瓶"}

ALLOWED_ORDER_FIELDS = {
	"item_code": "item.name",
	"item_name": "item.item_name",
	"modified": "item.modified",
	"modified_by": "item.modified_by",
	"creation": "item.creation",
}


def _parse_json_data(kwargs):
	jd = kwargs.get("json_data")
	if jd is None:
		jd = kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except (TypeError, ValueError):
			jd = {}
	return jd if isinstance(jd, dict) else {}


def _to_int(val, default):
	try:
		return int(val)
	except (TypeError, ValueError):
		return default


def _to_float_or_none(val):
	if val in (None, ""):
		return None
	try:
		return float(val)
	except (TypeError, ValueError):
		return None


def _resolve_table_form(category, table_form):
	tf = (table_form or "").strip().lower()
	if tf in ("default", "form2", "form3", "form4"):
		return tf
	if category in FORM2_CATEGORIES:
		return "form2"
	if category in FORM3_CATEGORIES:
		return "form3"
	if category in FORM4_CATEGORIES:
		return "form4"
	return "default"


def _normalize_order_by(order_by):
	raw = (order_by or "item_code asc").strip()
	parts = raw.split()
	field = parts[0] if parts else "item_code"
	direction = "asc"
	if len(parts) > 1 and parts[1].lower() in ("asc", "desc"):
		direction = parts[1].lower()
	sql_field = ALLOWED_ORDER_FIELDS.get(field, "item.name")
	return "{} {}".format(sql_field, direction)


def _get_category_item_names(category, search_item=None, order_by_sql="item.name asc", limit_start=0, limit_page_length=50):
	conditions = ["ps.br_process = %(category)s"]
	values = {"category": category}
	search_text = (search_item or "").strip()
	if search_text:
		conditions.append("(item.name LIKE %(kw)s OR item.item_name LIKE %(kw)s)")
		values["kw"] = "%{}%".format(search_text)

	where_sql = " AND ".join(conditions)
	count_sql = """
		SELECT COUNT(DISTINCT item.name) AS cnt
		FROM `tabItem` item
		INNER JOIN `tabBR Item Process Supplier` ps ON ps.parent = item.name
		WHERE {where_sql}
	""".format(where_sql=where_sql)
	total_count = 0
	cnt_rows = frappe.db.sql(count_sql, values, as_dict=True)
	if cnt_rows:
		total_count = int(cnt_rows[0].get("cnt") or 0)

	data_sql = """
		SELECT DISTINCT item.name AS item_code, item.item_name, item.modified, item.modified_by, item.br_quality_inspection
		FROM `tabItem` item
		INNER JOIN `tabBR Item Process Supplier` ps ON ps.parent = item.name
		WHERE {where_sql}
		ORDER BY {order_by_sql}
		LIMIT %(limit_start)s, %(limit_page_length)s
	""".format(where_sql=where_sql, order_by_sql=order_by_sql)
	values["limit_start"] = limit_start
	values["limit_page_length"] = limit_page_length
	rows = frappe.db.sql(data_sql, values, as_dict=True)
	return rows, total_count


def _get_expanded_total_count(category, search_item=None):
	conditions = ["ps.br_process = %(category)s"]
	values = {"category": category}
	search_text = (search_item or "").strip()
	if search_text:
		conditions.append("(item.name LIKE %(kw)s OR item.item_name LIKE %(kw)s)")
		values["kw"] = "%{}%".format(search_text)
	where_sql = " AND ".join(conditions)
	sql = """
		SELECT COALESCE(SUM(CASE WHEN c.cnt > 0 THEN c.cnt ELSE 1 END), 0) AS cnt
		FROM (
			SELECT DISTINCT item.name
			FROM `tabItem` item
			INNER JOIN `tabBR Item Process Supplier` ps ON ps.parent = item.name
			WHERE {where_sql}
		) t
		LEFT JOIN (
			SELECT parent, COUNT(*) AS cnt
			FROM `tabBR Item Cost Detail`
			GROUP BY parent
		) c ON c.parent = t.name
	""".format(where_sql=where_sql)
	rows = frappe.db.sql(sql, values, as_dict=True)
	return int((rows[0].get("cnt") if rows else 0) or 0)


def _get_cost_rows_by_item(item_codes):
	if not item_codes:
		return {}
	rows = frappe.get_all(
		"BR Item Cost Detail",
		filters={"parent": ["in", item_codes]},
		fields=[
			"parent",
			"idx",
			"br_injection_molding_per_day",
			"br_cavity_count",
			"br_cycle_time",
			"br_raw_material",
			"br_price_per_gram",
			"br_weight_grams",
			"br_material_cost_yuan",
			"br_seconds_per_hour",
			"br_daily_output",
			"br_unit_product_cost",
			"br_auditor",
			"br_audit_status",
		],
		order_by="parent asc, idx asc",
	)
	out = {}
	for r in rows:
		parent = r.get("parent")
		if parent:
			out.setdefault(parent, []).append(r)
	return out


def _get_process_rows_by_item(item_codes, category):
	if not item_codes:
		return {}
	rows = frappe.get_all(
		"BR Item Process Supplier",
		filters={"parent": ["in", item_codes], "br_process": category},
		fields=[
			"parent",
			"idx",
			"br_process",
			"br_workstation",
			"br_supplier_one",
			"br_price_one",
			"br_supplier_two",
			"br_price_two",
			"br_supplier_three",
			"br_price_three",
		],
		order_by="parent asc, idx asc",
	)
	out = {}
	for r in rows:
		parent = r.get("parent")
		if parent:
			out.setdefault(parent, []).append(r)
	return out


def _build_form2_suppliers(process_rows, keep_workstation=True):
	slots = []
	for r in process_rows or []:
		process_name = (r.get("br_process") or "").strip()
		work_station = (r.get("br_workstation") or "").strip() if keep_workstation else ""
		for idx in ("one", "two", "three"):
			price = _to_float_or_none(r.get("br_price_{}".format(idx)))
			if price is None:
				continue
			slots.append(
				{
					"process": process_name,
					"work_station": work_station,
					"unit_price": price,
				}
			)
	return slots[:3]


def _build_form4_suppliers(process_rows, weight_grams):
	slots = []
	for r in process_rows or []:
		process_name = (r.get("br_process") or "").strip()
		for idx in ("one", "two", "three"):
			price = _to_float_or_none(r.get("br_price_{}".format(idx)))
			if price is None:
				continue
			amount = None
			if weight_grams is not None:
				amount = round(weight_grams * price, 4)
			slots.append(
				{
					"process": process_name,
					"weight_grams": weight_grams,
					"unit_price": price,
					"amount": amount,
				}
			)
	return slots[:3]


def _build_row(item_row, category, table_form, row_no, cost, process_rows):
	weight_grams = _to_float_or_none(cost.get("br_weight_grams"))
	audit_status = (cost.get("br_audit_status") or "").strip() or "未审核"
	auditor = (cost.get("br_auditor") or "").strip()
	if not auditor:
		auditor = item_row.get("modified_by") or ""

	return {
		"row_no": row_no,
		"item_code": item_row.get("item_code"),
		"item_name": item_row.get("item_name") or "",
		"machine_cost_per_piece": _to_float_or_none(cost.get("br_injection_molding_per_day")),
		"output_per_shot": _to_float_or_none(cost.get("br_cavity_count")),
		"cycle_seconds": _to_float_or_none(cost.get("br_cycle_time")),
		"raw_material": (cost.get("br_raw_material") or "").strip(),
		"price_per_gram": _to_float_or_none(cost.get("br_price_per_gram")),
		"weight_grams": weight_grams,
		"material_cost_yuan": _to_float_or_none(cost.get("br_material_cost_yuan")),
		"cycle_per_hour": _to_float_or_none(cost.get("br_seconds_per_hour")),
		"daily_output": _to_float_or_none(cost.get("br_daily_output")),
		"estimated_cost_per_piece": _to_float_or_none(cost.get("br_unit_product_cost")),
		"last_modifier": item_row.get("modified_by") or "",
		"last_modify_time": str(item_row.get("modified") or ""),
		"auditor": auditor,
		"audit_status": audit_status,
		"form2_suppliers": _build_form2_suppliers(
			process_rows,
			keep_workstation=(table_form != "form3"),
		)
		if table_form in ("form2", "form3")
		else [],
		"form4_suppliers": _build_form4_suppliers(process_rows, weight_grams) if table_form == "form4" else [],
		"category": category,
		"table_form": table_form,
	}


@frappe.whitelist(allow_guest=False, methods=["POST"])
def get_material_details_by_category(**kwargs):
	"""
	按采购类目（工艺）查询物料明细。

	入参（支持 json_data）:
	- category: 必填，采购类目/工艺
	- table_form: 可选，default/form2/form3/form4
	- limit_start: 可选，默认 0
	- limit_page_length: 可选，默认 50
	- order_by: 可选，默认 item_code asc（仅白名单字段生效）
	- search_item: 可选，按物料编码/名称模糊搜索
	"""
	p = _parse_json_data(kwargs)
	category = (p.get("category") or p.get("process") or "").strip()
	if not category:
		frappe.throw("category is required")
	if category not in ALLOWED_CATEGORIES:
		frappe.throw("invalid category: {}".format(category))

	limit_start = max(0, _to_int(p.get("limit_start"), 0))
	limit_page_length = _to_int(p.get("limit_page_length"), 50)
	if limit_page_length <= 0:
		limit_page_length = 50
	limit_page_length = min(limit_page_length, 500)

	table_form = _resolve_table_form(category, p.get("table_form"))
	order_by_sql = _normalize_order_by(p.get("order_by"))
	search_item = p.get("search_item")

	item_rows, _ = _get_category_item_names(
		category=category,
		search_item=search_item,
		order_by_sql=order_by_sql,
		limit_start=limit_start,
		limit_page_length=limit_page_length,
	)
	total_count = _get_expanded_total_count(category=category, search_item=search_item)
	item_codes = [r.get("item_code") for r in item_rows if r.get("item_code")]
	cost_by_item = _get_cost_rows_by_item(item_codes)
	process_by_item = _get_process_rows_by_item(item_codes, category)

	message = []
	row_no = limit_start + 1
	for row in item_rows:
		item_code = row.get("item_code")
		cost_rows = cost_by_item.get(item_code) or []
		if not cost_rows:
			cost_rows = [{}]
		for cost in cost_rows:
			message.append(
				_build_row(
					item_row=row,
					category=category,
					table_form=table_form,
					row_no=row_no,
					cost=cost,
					process_rows=process_by_item.get(item_code) or [],
				)
			)
			row_no += 1

	frappe.response["message"] = message
	frappe.response["total_count"] = total_count
	return
