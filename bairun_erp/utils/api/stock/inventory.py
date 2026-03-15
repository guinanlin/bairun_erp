# Copyright (c) 2025, Bairun and contributors
# 生产仓库（成品/原材料/库存仓）接口：列表与写操作。
# 关联需求：《生产仓库（成品/原材料/库存仓）后端对接需求文档》与《生产仓库-成品原材料库存仓-需求分析与后端对接说明》
# 接口路径：bairun_erp.utils.api.stock.inventory

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import getdate

FINISHED_WAREHOUSE = "成品 - B"
RAW_MATERIAL_WAREHOUSE = "原材料仓 - B"
INVENTORY_WAREHOUSE = "库存仓 - B"

STATUS_PENDING_INBOUND = "pending_inbound"
STATUS_IN_STOCK = "in_stock"
STATUS_OUTBOUND = "outbound"

LIST_TYPE_FINISHED = "finished"
LIST_TYPE_RAW_MATERIAL = "raw_material"
LIST_TYPE_INVENTORY = "inventory"

_ORDER_COLUMNS = frozenset({
	"project_no", "item_code", "item_full_name", "warehouse",
	"received_qty", "posting_date", "actual_qty",
})


def _parse_params_inventory(kwargs):
	"""从 kwargs 或 json_data 解析分页、排序、筛选参数。"""
	params = {
		"order_by": "project_no asc, item_code asc",
		"limit_start": 0,
		"limit_page_length": 50,
		"search_project_no": None,
		"status": None,
		"list_type": LIST_TYPE_FINISHED,
		"include_reservation_details": False,
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

	params["order_by"] = (jd.get("order_by") or params["order_by"]).strip()
	params["limit_start"] = int(jd.get("limit_start", 0))
	params["limit_page_length"] = jd.get("limit_page_length", 50)
	params["search_project_no"] = jd.get("search_project_no")
	params["status"] = (jd.get("status") or "").strip().lower() or None
	params["include_reservation_details"] = bool(jd.get("include_reservation_details"))
	lt = (jd.get("list_type") or "").strip().lower()
	if lt == LIST_TYPE_RAW_MATERIAL:
		params["list_type"] = LIST_TYPE_RAW_MATERIAL
	elif lt == LIST_TYPE_INVENTORY:
		params["list_type"] = LIST_TYPE_INVENTORY
	else:
		params["list_type"] = LIST_TYPE_FINISHED
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


def _sanitize_order_by(order_by):
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


# ---------- 成品 ----------

def _get_finished_in_stock_rows(warehouse=FINISHED_WAREHOUSE):
	"""已入库：Bin 中 actual_qty > 0，按 (item_code, warehouse) 聚合；projectNo 从入该仓的 SE custom_customer_order 尝试带出。"""
	bin_rows = frappe.db.sql("""
		SELECT b.item_code, b.warehouse, b.actual_qty, b.reserved_qty
		FROM `tabBin` b
		WHERE b.warehouse = %s AND (b.actual_qty IS NULL OR b.actual_qty > 0)
	""", (warehouse,), as_dict=True)
	se_meta = frappe.get_meta("Stock Entry")
	has_customer_order = bool(se_meta.get_field("custom_customer_order"))
	project_by_item = {}
	if has_customer_order:
		se_rows = frappe.db.sql("""
			SELECT sed.item_code, se.custom_customer_order AS project_no
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND se.docstatus = 1
			WHERE sed.t_warehouse = %s
			ORDER BY se.posting_date DESC
		""", (warehouse,), as_dict=True)
		for r in se_rows:
			if r.get("item_code") and r.get("project_no") and r["item_code"] not in project_by_item:
				project_by_item[r["item_code"]] = r["project_no"]

	out = []
	for r in bin_rows:
		qty = _flt(r.get("actual_qty"))
		if qty <= 0:
			continue
		item_code = r.get("item_code") or ""
		wh = r.get("warehouse") or warehouse
		project_no = project_by_item.get(item_code, "") or ""
		out.append({
			"key": (project_no, item_code, wh),
			"actual_qty": qty,
			"reserved_qty": _flt(r.get("reserved_qty")),
		})
	return out


def _get_finished_outbound_rows(warehouse=FINISHED_WAREHOUSE):
	"""已出库：从成品仓转出的 SE 行 + Delivery Note 行，按 (project_no, item_code, warehouse) 聚合。"""
	se_meta = frappe.get_meta("Stock Entry")
	has_customer_order = bool(se_meta.get_field("custom_customer_order"))
	select_se = [
		"sed.item_code", "sed.s_warehouse AS warehouse", "sed.qty",
		"se.posting_date", "se.name AS se_name",
	]
	if has_customer_order:
		select_se.append("se.custom_customer_order AS project_no")
	else:
		select_se.append("'' AS project_no")

	agg = {}
	sql_se = """
		SELECT {}
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND se.docstatus = 1
		WHERE sed.s_warehouse = %s AND sed.qty > 0
	""".format(", ".join(select_se))
	for r in frappe.db.sql(sql_se, (warehouse,), as_dict=True):
		project_no = (r.get("project_no") or "").strip()
		item_code = r.get("item_code") or ""
		wh = r.get("warehouse") or warehouse
		key = (project_no, item_code, wh)
		if key not in agg:
			agg[key] = {"qty": 0, "posting_date": r.get("posting_date")}
		agg[key]["qty"] += _flt(r.get("qty"))

	dn_meta = frappe.get_meta("Delivery Note Item")
	if dn_meta.get_field("against_sales_order"):
		dn_sql = """
			SELECT dni.item_code, dni.warehouse, dni.qty, dni.against_sales_order AS project_no
			FROM `tabDelivery Note Item` dni
			INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent AND dn.docstatus = 1
			WHERE dni.warehouse = %s AND dni.qty > 0
		"""
		for r in frappe.db.sql(dn_sql, (warehouse,), as_dict=True):
			project_no = (r.get("project_no") or "").strip()
			item_code = r.get("item_code") or ""
			wh = r.get("warehouse") or warehouse
			key = (project_no, item_code, wh)
			if key not in agg:
				agg[key] = {"qty": 0, "posting_date": None}
			agg[key]["qty"] += _flt(r.get("qty"))

	out = []
	for key, v in agg.items():
		if v["qty"] > 0:
			out.append({"key": key, "qty": v["qty"], "posting_date": v["posting_date"]})
	return out


def _build_finished_row(key, in_stock_data=None, outbound_data=None, status="已入库"):
	"""组装成品行，字段与需求文档 §2.2 一致。"""
	project_no, item_code, warehouse = key
	row_id = "|".join([str(project_no or ""), str(item_code or ""), str(warehouse or "")])
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else ""
	item_name = (item_name or "").strip()
	item_full_name = item_name or item_code
	if item_name and item_name != item_code:
		item_full_name = "{} - {}".format(item_code, item_name)

	order_qty = 0
	received_qty = 0
	unreceived_qty = 0
	packing_qty = None
	box_config = None
	volume = None
	unit_price = 0
	warehouse_location = ""
	work_instruction_url = ""

	if in_stock_data:
		received_qty = _flt(in_stock_data.get("actual_qty"))
	if outbound_data:
		received_qty = _flt(outbound_data.get("qty"))

	item_doc = frappe.db.get_value(
		"Item", item_code,
		["valuation_rate", "standard_rate", "stock_uom"],
		as_dict=True
	) if item_code else None
	if item_doc:
		unit_price = _flt(item_doc.get("valuation_rate"))
		if unit_price == 0:
			unit_price = _flt(item_doc.get("standard_rate"))

	return {
		"id": row_id,
		"date": _date_str(outbound_data.get("posting_date") if outbound_data else None) or _date_str(getdate()),
		"projectNo": project_no or "",
		"itemFullName": item_full_name,
		"orderQty": order_qty,
		"receivedQty": received_qty,
		"unreceivedQty": unreceived_qty,
		"warehouse": warehouse or FINISHED_WAREHOUSE,
		"warehouseLocation": warehouse_location or "",
		"packingQty": packing_qty,
		"boxConfig": box_config,
		"volume": volume,
		"unitPrice": unit_price,
		"workInstructionUrl": work_instruction_url or "",
		"status": status,
	}


@frappe.whitelist()
def get_finished_list(**kwargs):
	"""
	成品列表：按状态 已入库(in_stock) / 已出库(outbound)，支持销售订单号筛选与分页。
	POST json_data: status, search_project_no, limit_start, limit_page_length, order_by
	返回: message = [ 行对象 ], total_count
	"""
	if not frappe.has_permission("Warehouse", "read"):
		frappe.throw(frappe._("No permission to read Warehouse"))
	params = _parse_params_inventory(kwargs)
	status = params.get("status") or STATUS_IN_STOCK
	search = (params.get("search_project_no") or "").strip()
	limit_start = max(0, int(params["limit_start"]))
	limit_page_length = int(params.get("limit_page_length") or 50)
	use_limit = limit_page_length > 0

	candidates = []
	if status == STATUS_OUTBOUND:
		rows = _get_finished_outbound_rows(FINISHED_WAREHOUSE)
		for r in rows:
			key = r["key"]
			if search and search.lower() not in (key[0] or "").lower():
				continue
			candidates.append((key, None, r))
		status_label = "已出库"
	else:
		rows = _get_finished_in_stock_rows(FINISHED_WAREHOUSE)
		for r in rows:
			key = r["key"]
			if search and search.lower() not in (key[0] or "").lower():
				continue
			candidates.append((key, r, None))
		status_label = "已入库"

	def sort_key(item):
		k = item[0]
		return (k[0] or "", k[1] or "", k[2] or "")
	candidates.sort(key=sort_key)
	total_count = len(candidates)

	if use_limit:
		page = candidates[limit_start: limit_start + limit_page_length]
	else:
		page = candidates[limit_start:]

	out = []
	for item in page:
		key, in_data, out_data = item
		out.append(_build_finished_row(key, in_stock_data=in_data, outbound_data=out_data, status=status_label))

	frappe.response["message"] = out
	frappe.response["total_count"] = total_count


def _parse_finished_ids(ids):
	"""解析成品行 id（projectNo|item_code|warehouse）为 [(item_code, qty_from_bin), ...]。"""
	if not ids:
		return []
	if isinstance(ids, str):
		ids = [x.strip() for x in ids.split(",") if x.strip()]
	rows = []
	for row_id in ids:
		parts = (row_id or "").split("|")
		if len(parts) >= 2:
			item_code = parts[1].strip()
			if item_code:
				rows.append({"id": row_id, "item_code": item_code, "project_no": parts[0].strip() if parts[0] else ""})
	return rows


@frappe.whitelist()
def outbound_finished(**kwargs):
	"""成品出库：勾选已入库行，扣减成品仓库存并生成 Material Issue 类 Stock Entry。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_finished_ids(ids)
	if not parsed:
		return {"success": False, "error": "No valid ids"}
	# 按 item_code 汇总数量，从 Bin 取可出库数量
	item_qty = {}
	for p in parsed:
		ic = p["item_code"]
		bin_val = frappe.db.get_value(
			"Bin",
			{"item_code": ic, "warehouse": FINISHED_WAREHOUSE},
			"actual_qty",
			as_dict=False
		)
		available = _flt(bin_val)
		if available <= 0:
			continue
		item_qty[ic] = item_qty.get(ic, 0) + min(1, available)
	# 简化：每行出库 1 单位（或可改为前端传 qty）
	if not item_qty:
		return {"success": False, "error": "No stock available for selected rows"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"from_warehouse": FINISHED_WAREHOUSE,
		})
		for item_code, qty in item_qty.items():
			se.append("items", {
				"item_code": item_code,
				"qty": qty,
				"s_warehouse": FINISHED_WAREHOUSE,
			})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def transfer_finished_to_inventory(**kwargs):
	"""成品移库：成品仓 → 库存仓，生成 Material Transfer 类 Stock Entry。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	target_warehouse = (jd.get("target_warehouse") or "").strip() or INVENTORY_WAREHOUSE
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_finished_ids(ids)
	if not parsed:
		return {"success": False, "error": "No valid ids"}
	item_qty = {}
	for p in parsed:
		ic = p["item_code"]
		bin_val = frappe.db.get_value("Bin", {"item_code": ic, "warehouse": FINISHED_WAREHOUSE}, "actual_qty")
		available = _flt(bin_val)
		if available <= 0:
			continue
		item_qty[ic] = item_qty.get(ic, 0) + min(1, available)
	if not item_qty:
		return {"success": False, "error": "No stock available for selected rows"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Transfer",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
		})
		for item_code, qty in item_qty.items():
			se.append("items", {
				"item_code": item_code,
				"qty": qty,
				"s_warehouse": FINISHED_WAREHOUSE,
				"t_warehouse": target_warehouse,
			})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


# ---------- 原材料 ----------

def _get_raw_material_pending_inbound():
	"""待入库：PO 行 warehouse=原材料仓-B 且 received_qty < qty。"""
	sql = """
		SELECT po.name AS purchase_order, poi.item_code, poi.stock_qty AS qty,
		       IFNULL(poi.received_qty, 0) AS received_qty,
		       poi.rate, poi.warehouse, po.supplier, po.transaction_date
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		WHERE po.docstatus = 1 AND poi.warehouse = %s
		  AND (IFNULL(poi.received_qty, 0) < IFNULL(poi.stock_qty, poi.qty))
	"""
	return frappe.db.sql(sql, (RAW_MATERIAL_WAREHOUSE,), as_dict=True)


def _get_raw_material_in_stock():
	"""已入库：Bin 原材料仓-B actual_qty > 0。"""
	return frappe.db.sql("""
		SELECT b.item_code, b.warehouse, b.actual_qty
		FROM `tabBin` b
		WHERE b.warehouse = %s AND (b.actual_qty IS NULL OR b.actual_qty > 0)
	""", (RAW_MATERIAL_WAREHOUSE,), as_dict=True)


def _get_raw_material_outbound():
	"""已出库：从原材料仓发出的 SE 行汇总。"""
	sql = """
		SELECT sed.item_code, sed.s_warehouse AS warehouse, SUM(sed.qty) AS qty
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND se.docstatus = 1
		WHERE sed.s_warehouse = %s AND sed.qty > 0
		GROUP BY sed.item_code, sed.s_warehouse
	"""
	return frappe.db.sql(sql, (RAW_MATERIAL_WAREHOUSE,), as_dict=True)


def _build_raw_material_row(record, status, warehouse=RAW_MATERIAL_WAREHOUSE):
	"""组装原材料行，与 material/item.py _item_to_raw_material_row 字段一致 + status。"""
	item_code = record.get("item_code") or ""
	item = frappe.db.get_value(
		"Item", item_code,
		["item_name", "valuation_rate", "standard_rate", "stock_uom"],
		as_dict=True
	) if item_code else None
	item_name = (item.get("item_name") or item_code) if item else item_code
	item_full_name = item_name
	if item and item.get("item_name") and item.get("item_name") != item_code:
		item_full_name = "{} - {}".format(item_code, item.get("item_name"))
	date_val = record.get("transaction_date") or record.get("posting_date") or getdate()
	date_str = _date_str(date_val)
	project_no = record.get("purchase_order") or record.get("projectNo") or ""
	order_qty = _flt(record.get("order_qty") or record.get("qty"))
	received_qty = _flt(record.get("received_qty"))
	unreceived_qty = max(0, order_qty - received_qty)
	in_stock_qty = _flt(record.get("actual_qty"))
	unit_price = _flt(record.get("rate") or record.get("unit_price"))
	supplier = record.get("supplier") or ""
	supplier_name = (record.get("supplier_name") or "") or (frappe.db.get_value("Supplier", supplier, "supplier_name") if supplier else "") or ""
	wh = record.get("warehouse") or warehouse
	row_id = "|".join([str(project_no), str(item_code), str(wh)])
	return {
		"id": row_id,
		"date": date_str or "",
		"projectNo": project_no,
		"itemFullName": item_full_name,
		"orderQty": order_qty,
		"unitPrice": unit_price,
		"receivedQty": received_qty,
		"unreceivedQty": unreceived_qty,
		"inStockQty": in_stock_qty,
		"supplierId": supplier or "",
		"supplier": supplier_name,
		"inventoryCost": _flt(item.get("valuation_rate")) if item else 0,
		"salesPrice": _flt(item.get("standard_rate")) if item else 0,
		"warehouse": wh,
		"warehouseLocation": "",
		"unit": (item.get("stock_uom") or "") if item else "",
		"workInstructionUrl": "",
		"status": status,
	}


@frappe.whitelist()
def get_raw_material_list(**kwargs):
	"""
	原材料列表：按状态 待入库/已入库/已出库，采购单号筛选，分页。返回字段与 get_raw_material_item 一致。
	POST json_data: status, search_project_no, limit_start, limit_page_length
	返回: message = [ 行对象 ], total_count
	"""
	if not frappe.has_permission("Warehouse", "read"):
		frappe.throw(frappe._("No permission to read Warehouse"))
	params = _parse_params_inventory(kwargs)
	status = params.get("status") or STATUS_PENDING_INBOUND
	search = (params.get("search_project_no") or "").strip()
	limit_start = max(0, int(params["limit_start"]))
	limit_page_length = int(params.get("limit_page_length") or 50)
	use_limit = limit_page_length > 0

	candidates = []
	if status == STATUS_PENDING_INBOUND:
		rows = _get_raw_material_pending_inbound()
		for r in rows:
			if search and search.lower() not in (r.get("purchase_order") or "").lower():
				continue
			r["order_qty"] = _flt(r.get("qty"))
			r["received_qty"] = _flt(r.get("received_qty"))
			candidates.append((r, "待入库"))
	elif status == STATUS_OUTBOUND:
		rows = _get_raw_material_outbound()
		for r in rows:
			r["actual_qty"] = 0
			r["projectNo"] = ""
			r["order_qty"] = 0
			r["received_qty"] = _flt(r.get("qty"))
			candidates.append((r, "已出库"))
	else:
		rows = _get_raw_material_in_stock()
		# 关联 PR 取 projectNo（采购单号）
		pr_map = {}
		for r in rows:
			ic, wh = r.get("item_code"), r.get("warehouse")
			pr_row = frappe.db.sql("""
				SELECT pri.parent AS pr_name, pr.name AS purchase_order
				FROM `tabPurchase Receipt Item` pri
				INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent AND pr.docstatus = 1
				WHERE pri.item_code = %s AND pri.warehouse = %s
				ORDER BY pr.posting_date DESC LIMIT 1
			""", (ic, wh or RAW_MATERIAL_WAREHOUSE), as_dict=True)
			if pr_row:
				pr_map[ic] = pr_row[0].get("purchase_order") or ""
		for r in rows:
			r["projectNo"] = pr_map.get(r.get("item_code"), "")
			r["order_qty"] = _flt(r.get("actual_qty"))
			r["received_qty"] = _flt(r.get("actual_qty"))
			if search and search.lower() not in (r.get("projectNo") or "").lower():
				continue
			candidates.append((r, "已入库"))

	candidates.sort(key=lambda x: ((x[0].get("purchase_order") or x[0].get("projectNo") or ""), (x[0].get("item_code") or "")))
	total_count = len(candidates)
	if use_limit:
		page = candidates[limit_start: limit_start + limit_page_length]
	else:
		page = candidates[limit_start:]

	out = [_build_raw_material_row(rec, st) for rec, st in page]
	frappe.response["message"] = out
	frappe.response["total_count"] = total_count


def _parse_raw_or_inventory_ids(ids):
	if not ids:
		return []
	if isinstance(ids, str):
		ids = [x.strip() for x in ids.split(",") if x.strip()]
	rows = []
	for row_id in ids:
		parts = (row_id or "").split("|")
		if len(parts) >= 2:
			rows.append({
				"id": row_id,
				"project_no": parts[0].strip() if parts[0] else "",
				"item_code": parts[1].strip() if len(parts) > 1 else "",
				"warehouse": parts[2].strip() if len(parts) > 2 else "",
			})
	return rows


@frappe.whitelist()
def inbound_raw_material(**kwargs):
	"""原材料入库：勾选待入库行，生成 Stock Entry 入原材料仓（或更新 PR）。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_raw_or_inventory_ids(ids)
	# 根据 project_no + item_code 找到 PO 行，创建 PR 或 SE 入库
	to_receive = []
	for p in parsed:
		po_name, item_code = p.get("project_no"), p.get("item_code")
		if not item_code:
			continue
		if po_name:
			po_item = frappe.db.sql("""
				SELECT poi.name, poi.parent, poi.item_code, poi.stock_qty - IFNULL(poi.received_qty, 0) AS pending
				FROM `tabPurchase Order Item` poi
				WHERE poi.parent = %s AND poi.item_code = %s AND poi.warehouse = %s
				  AND (IFNULL(poi.received_qty, 0) < IFNULL(poi.stock_qty, poi.qty))
			""", (po_name, item_code, RAW_MATERIAL_WAREHOUSE), as_dict=True)
			if po_item and _flt(po_item[0].get("pending")) > 0:
				to_receive.append({"po": po_item[0].get("parent"), "item_code": item_code, "qty": _flt(po_item[0].get("pending"))})
		else:
			to_receive.append({"po": None, "item_code": item_code, "qty": 1})
	if not to_receive:
		return {"success": False, "error": "No pending rows to receive"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Receipt",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"to_warehouse": RAW_MATERIAL_WAREHOUSE,
		})
		for r in to_receive:
			se.append("items", {"item_code": r["item_code"], "qty": r["qty"], "t_warehouse": RAW_MATERIAL_WAREHOUSE})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def outbound_raw_material(**kwargs):
	"""原材料出库：勾选已入库行，扣减原材料仓，生成 Material Issue。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_raw_or_inventory_ids(ids)
	item_qty = {}
	for p in parsed:
		ic = p.get("item_code")
		if not ic:
			continue
		bin_val = frappe.db.get_value("Bin", {"item_code": ic, "warehouse": RAW_MATERIAL_WAREHOUSE}, "actual_qty")
		available = _flt(bin_val)
		if available <= 0:
			continue
		item_qty[ic] = item_qty.get(ic, 0) + min(1, available)
	if not item_qty:
		return {"success": False, "error": "No stock available"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"from_warehouse": RAW_MATERIAL_WAREHOUSE,
		})
		for item_code, qty in item_qty.items():
			se.append("items", {"item_code": item_code, "qty": qty, "s_warehouse": RAW_MATERIAL_WAREHOUSE})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


# ---------- 库存仓 ----------

def _get_inventory_pending_inbound():
	sql = """
		SELECT po.name AS purchase_order, poi.item_code, poi.stock_qty AS qty,
		       IFNULL(poi.received_qty, 0) AS received_qty,
		       poi.rate, poi.warehouse, po.supplier, po.transaction_date
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		WHERE po.docstatus = 1 AND poi.warehouse = %s
		  AND (IFNULL(poi.received_qty, 0) < IFNULL(poi.stock_qty, poi.qty))
	"""
	return frappe.db.sql(sql, (INVENTORY_WAREHOUSE,), as_dict=True)


def _get_inventory_in_stock():
	return frappe.db.sql("""
		SELECT b.item_code, b.warehouse, b.actual_qty, b.reserved_qty
		FROM `tabBin` b
		WHERE b.warehouse = %s AND (b.actual_qty IS NULL OR b.actual_qty > 0)
	""", (INVENTORY_WAREHOUSE,), as_dict=True)


def _get_inventory_outbound():
	sql = """
		SELECT sed.item_code, sed.s_warehouse AS warehouse, SUM(sed.qty) AS qty
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent AND se.docstatus = 1
		WHERE sed.s_warehouse = %s AND sed.qty > 0
		GROUP BY sed.item_code, sed.s_warehouse
	"""
	return frappe.db.sql(sql, (INVENTORY_WAREHOUSE,), as_dict=True)


def _get_reservation_details(item_code, warehouse):
	"""预留明细：Bin.reserved_qty + 若有 Stock Reservation Entry 则组装；否则返回空数组。"""
	out = []
	try:
		if frappe.db.table_exists("Stock Reservation Entry"):
			rows = frappe.db.sql("""
				SELECT sre.name, sre.reserved_qty, sre.voucher_type, sre.voucher_no
				FROM `tabStock Reservation Entry` sre
				WHERE sre.item_code = %s AND sre.warehouse = %s AND sre.docstatus = 1
			""", (item_code, warehouse), as_dict=True)
			for r in rows:
				so_name = r.get("voucher_no") if r.get("voucher_type") == "Sales Order" else ""
				customer = ""
				if so_name:
					customer = frappe.db.get_value("Sales Order", so_name, "customer") or ""
				out.append({
					"salesContract": so_name or "",
					"productName": item_code,
					"reservedQty": _flt(r.get("reserved_qty")),
					"customer": customer or "",
				})
	except Exception:
		pass
	return out


def _build_inventory_row(record, status, include_reservation=False):
	"""组装库存仓行，与需求文档 §4.2 一致；可选 reservationDetails。"""
	item_code = record.get("item_code") or ""
	item = frappe.db.get_value("Item", item_code, ["item_name", "valuation_rate", "stock_uom"], as_dict=True) if item_code else None
	item_name = (item.get("item_name") or item_code) if item else item_code
	item_full_name = "{} - {}".format(item_code, item_name) if (item and item.get("item_name") != item_code) else item_name
	date_val = record.get("transaction_date") or record.get("posting_date") or getdate()
	project_no = record.get("purchase_order") or record.get("projectNo") or ""
	order_qty = _flt(record.get("order_qty") or record.get("qty"))
	received_qty = _flt(record.get("received_qty"))
	unreceived_qty = max(0, order_qty - received_qty)
	in_stock_qty = _flt(record.get("actual_qty"))
	reserved_qty = _flt(record.get("reserved_qty"))
	wh = record.get("warehouse") or INVENTORY_WAREHOUSE
	row_id = "|".join([str(project_no), str(item_code), str(wh)])
	row = {
		"id": row_id,
		"date": _date_str(date_val) or "",
		"projectNo": project_no,
		"itemFullName": item_full_name,
		"orderQty": order_qty,
		"receivedQty": received_qty,
		"unreceivedQty": unreceived_qty,
		"inStockQty": in_stock_qty,
		"reservedQty": reserved_qty,
		"inventoryCost": _flt(item.get("valuation_rate")) if item else 0,
		"supplierId": record.get("supplier") or "",
		"supplier": (record.get("supplier_name") or "") or (frappe.db.get_value("Supplier", record.get("supplier"), "supplier_name") if record.get("supplier") else "") or "",
		"warehouse": wh,
		"warehouseLocation": "",
		"unitPrice": _flt(record.get("rate")),
		"workInstructionUrl": "",
		"status": status,
	}
	if include_reservation:
		row["reservationDetails"] = _get_reservation_details(item_code, wh)
	return row


@frappe.whitelist()
def get_inventory_list(**kwargs):
	"""
	库存仓列表：按状态 待入库/已入库/已出库，采购订单号筛选；可选 include_reservation_details。
	返回: message = [ 行对象 ], total_count
	"""
	if not frappe.has_permission("Warehouse", "read"):
		frappe.throw(frappe._("No permission to read Warehouse"))
	params = _parse_params_inventory(kwargs)
	status = params.get("status") or STATUS_PENDING_INBOUND
	search = (params.get("search_project_no") or "").strip()
	limit_start = max(0, int(params["limit_start"]))
	limit_page_length = int(params.get("limit_page_length") or 50)
	use_limit = limit_page_length > 0
	include_res = params.get("include_reservation_details")

	candidates = []
	if status == STATUS_PENDING_INBOUND:
		rows = _get_inventory_pending_inbound()
		for r in rows:
			if search and search.lower() not in (r.get("purchase_order") or "").lower():
				continue
			r["order_qty"] = _flt(r.get("qty"))
			r["received_qty"] = _flt(r.get("received_qty"))
			candidates.append((r, "待入库"))
	elif status == STATUS_OUTBOUND:
		rows = _get_inventory_outbound()
		for r in rows:
			r["actual_qty"] = 0
			r["projectNo"] = ""
			r["order_qty"] = 0
			r["received_qty"] = _flt(r.get("qty"))
			candidates.append((r, "已出库"))
	else:
		rows = _get_inventory_in_stock()
		pr_map = {}
		for r in rows:
			pr_row = frappe.db.sql("""
				SELECT pr.name AS purchase_order FROM `tabPurchase Receipt Item` pri
				INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent AND pr.docstatus = 1
				WHERE pri.item_code = %s AND pri.warehouse = %s
				ORDER BY pr.posting_date DESC LIMIT 1
			""", (r.get("item_code"), r.get("warehouse") or INVENTORY_WAREHOUSE), as_dict=True)
			if pr_row:
				pr_map[r.get("item_code")] = pr_row[0].get("purchase_order") or ""
		for r in rows:
			r["projectNo"] = pr_map.get(r.get("item_code"), "")
			r["order_qty"] = _flt(r.get("actual_qty"))
			r["received_qty"] = _flt(r.get("actual_qty"))
			if search and search.lower() not in (r.get("projectNo") or "").lower():
				continue
			candidates.append((r, "已入库"))

	candidates.sort(key=lambda x: ((x[0].get("purchase_order") or x[0].get("projectNo") or ""), (x[0].get("item_code") or "")))
	total_count = len(candidates)
	if use_limit:
		page = candidates[limit_start: limit_start + limit_page_length]
	else:
		page = candidates[limit_start:]

	out = [_build_inventory_row(rec, st, include_reservation=include_res) for rec, st in page]
	frappe.response["message"] = out
	frappe.response["total_count"] = total_count


@frappe.whitelist()
def get_inventory_reservation_details(**kwargs):
	"""预留明细：按 row_id 或 item_code+warehouse 返回 reservationDetails。"""
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"reservationDetails": []}
	row_id = jd.get("row_id")
	item_code = jd.get("item_code")
	warehouse = (jd.get("warehouse") or "").strip() or INVENTORY_WAREHOUSE
	if row_id:
		parts = (row_id or "").split("|")
		if len(parts) >= 2:
			item_code = parts[1].strip()
		if len(parts) >= 3:
			warehouse = parts[2].strip() or INVENTORY_WAREHOUSE
	if not item_code:
		return {"reservationDetails": []}
	details = _get_reservation_details(item_code, warehouse)
	return {"reservationDetails": details}


@frappe.whitelist()
def submit_inventory_to_warehouse(**kwargs):
	"""仓库提交：勾选待入库行提交给仓库；首版仅返回成功，不落库不改状态。"""
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_raw_or_inventory_ids(ids)
	# 业务语义：仅标记提交，不扣库存；若无自定义字段则仅校验 id 有效
	return {"success": True, "submitted_count": len(parsed)}


@frappe.whitelist()
def inbound_inventory(**kwargs):
	"""统计入库：勾选待入库行，状态改为已入库并写入库存仓。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_raw_or_inventory_ids(ids)
	to_receive = []
	for p in parsed:
		po_name, item_code = p.get("project_no"), p.get("item_code")
		if not item_code:
			continue
		if po_name:
			po_item = frappe.db.sql("""
				SELECT poi.parent, poi.item_code, poi.stock_qty - IFNULL(poi.received_qty, 0) AS pending
				FROM `tabPurchase Order Item` poi
				WHERE poi.parent = %s AND poi.item_code = %s AND poi.warehouse = %s
				  AND (IFNULL(poi.received_qty, 0) < IFNULL(poi.stock_qty, poi.qty))
			""", (po_name, item_code, INVENTORY_WAREHOUSE), as_dict=True)
			if po_item and _flt(po_item[0].get("pending")) > 0:
				to_receive.append({"item_code": item_code, "qty": _flt(po_item[0].get("pending"))})
		else:
			to_receive.append({"item_code": item_code, "qty": 1})
	if not to_receive:
		return {"success": False, "error": "No pending rows to receive"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Receipt",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"to_warehouse": INVENTORY_WAREHOUSE,
		})
		for r in to_receive:
			se.append("items", {"item_code": r["item_code"], "qty": r["qty"], "t_warehouse": INVENTORY_WAREHOUSE})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def outbound_inventory(**kwargs):
	"""库存仓出库：勾选已入库行，扣减库存仓。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	ids = jd.get("ids")
	if not ids:
		return {"success": False, "error": "ids required"}
	parsed = _parse_raw_or_inventory_ids(ids)
	item_qty = {}
	for p in parsed:
		ic = p.get("item_code")
		if not ic:
			continue
		bin_val = frappe.db.get_value("Bin", {"item_code": ic, "warehouse": INVENTORY_WAREHOUSE}, "actual_qty")
		available = _flt(bin_val)
		if available <= 0:
			continue
		item_qty[ic] = item_qty.get(ic, 0) + min(1, available)
	if not item_qty:
		return {"success": False, "error": "No stock available"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"from_warehouse": INVENTORY_WAREHOUSE,
		})
		for item_code, qty in item_qty.items():
			se.append("items", {"item_code": item_code, "qty": qty, "s_warehouse": INVENTORY_WAREHOUSE})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def other_inbound_inventory(**kwargs):
	"""其他入库：业务原因、来源、单据号、物料、数量、库位、备注 → 生成库存仓入库 Stock Entry。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	reason = (jd.get("reason") or jd.get("业务原因") or "").strip()
	source = (jd.get("source") or jd.get("来源") or "").strip()
	ref_no = (jd.get("ref_no") or jd.get("单据号") or "").strip()
	item_code = (jd.get("item_code") or jd.get("物料") or "").strip()
	qty = _flt(jd.get("qty") or jd.get("数量"))
	location = (jd.get("warehouse_location") or jd.get("库位") or "").strip()
	remarks = (jd.get("remarks") or jd.get("备注") or "").strip()
	if not item_code or qty <= 0:
		return {"success": False, "error": "item_code and qty required"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Receipt",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"to_warehouse": INVENTORY_WAREHOUSE,
			"remarks": "{} {} {}".format(reason, source, ref_no).strip() or remarks,
		})
		se.append("items", {
			"item_code": item_code,
			"qty": qty,
			"t_warehouse": INVENTORY_WAREHOUSE,
		})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def other_outbound_inventory(**kwargs):
	"""其他出库：业务原因、去向、单据号、物料、数量、备注 → 扣减库存仓。"""
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(frappe._("No permission to create Stock Entry"))
	jd = kwargs.get("json_data") or kwargs
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except json.JSONDecodeError:
			return {"success": False, "error": "Invalid json_data"}
	reason = (jd.get("reason") or jd.get("业务原因") or "").strip()
	dest = (jd.get("dest") or jd.get("去向") or "").strip()
	ref_no = (jd.get("ref_no") or jd.get("单据号") or "").strip()
	item_code = (jd.get("item_code") or jd.get("物料") or "").strip()
	qty = _flt(jd.get("qty") or jd.get("数量"))
	remarks = (jd.get("remarks") or jd.get("备注") or "").strip()
	if not item_code or qty <= 0:
		return {"success": False, "error": "item_code and qty required"}
	bin_val = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": INVENTORY_WAREHOUSE}, "actual_qty")
	if _flt(bin_val) < qty:
		return {"success": False, "error": "Insufficient stock"}
	try:
		frappe.db.begin()
		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"purpose": "Material Issue",
			"company": frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company"),
			"from_warehouse": INVENTORY_WAREHOUSE,
			"remarks": "{} {} {}".format(reason, dest, ref_no).strip() or remarks,
		})
		se.append("items", {"item_code": item_code, "qty": qty, "s_warehouse": INVENTORY_WAREHOUSE})
		se.flags.ignore_validate_update_after_submit = True
		se.insert()
		se.submit()
		frappe.db.commit()
		return {"success": True, "stock_entry": se.name}
	except Exception as e:
		frappe.db.rollback()
		return {"success": False, "error": str(e)}
