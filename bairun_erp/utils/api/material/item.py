# Copyright (c) 2025, Bairun and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import json
import frappe
from frappe.utils import getdate


def _item_to_raw_material_row(item_code, warehouse=None):
	"""将一条 Item 转成原材料主列表的一行（按 item-field-mapping 映射）。"""
	if not frappe.db.exists("Item", item_code):
		return None
	item = frappe.get_cached_doc("Item", item_code)

	creation = item.creation
	date_str = getdate(creation).strftime("%Y-%m-%d") if creation else ""

	item_full_name = item.item_name or item_code
	if item.item_name and item.item_name != item_code:
		item_full_name = f"{item_code} - {item.item_name}"

	in_stock_qty = 0.0
	bin_cond = "item_code = %(item_code)s"
	bin_params = {"item_code": item_code}
	if warehouse:
		bin_cond += " AND warehouse = %(warehouse)s"
		bin_params["warehouse"] = warehouse
	result = frappe.db.sql(
		f"SELECT COALESCE(SUM(actual_qty), 0) AS total FROM `tabBin` WHERE {bin_cond}",
		bin_params,
		as_dict=True,
	)
	if result and result[0].get("total") is not None:
		in_stock_qty = float(result[0]["total"])

	default_warehouse = ""
	for d in (item.item_defaults or []):
		if d.get("default_warehouse"):
			default_warehouse = d.default_warehouse
			break

	unit = item.stock_uom or ""

	return {
		"id": item_code,
		"date": date_str,
		"projectNo": "",
		"itemFullName": item_full_name,
		"orderQty": 0,
		"unitPrice": 0,
		"receivedQty": 0,
		"unreceivedQty": 0,
		"inStockQty": in_stock_qty,
		"supplierId": "",
		"supplier": "",
		"inventoryCost": float(item.valuation_rate or 0),
		"salesPrice": float(item.standard_rate or 0),
		"warehouse": default_warehouse,
		"warehouseLocation": "",
		"unit": unit,
		"workInstructionUrl": "",
		"status": "",
	}


@frappe.whitelist()
def get_raw_material_item(**args):
	"""
	原材料物料接口：
	- 传入 item_code：返回该物料一条详情（主列表字段）。
	- 不传 item_code：返回所有原材料的列表，分页。

	Args:
		**args:
			item_code: 可选。物料编码，传则只返回该条。
			warehouse: 可选。指定仓库时在库数只汇总该仓库。
			page: 可选。页码，从 1 开始，默认 1。
			page_size: 可选。每页条数，默认 20。

	Returns:
		有 item_code 时: dict 单条（主列表字段）。
		无 item_code 时: { "data": [...], "total": N, "page": P, "page_size": S }。
	"""
	item_code = args.get("item_code")
	warehouse = args.get("warehouse")
	page = max(1, int(args.get("page") or args.get("page_number") or 1))
	page_size = max(1, min(500, int(args.get("page_size") or args.get("limit_page_length") or 20)))

	if item_code:
		if not frappe.db.exists("Item", item_code):
			return {"error": f"Item {item_code} does not exist"}
		return _item_to_raw_material_row(item_code, warehouse)

	# 列表 + 分页：所有未禁用的 Item 视为原材料
	filters = {"disabled": 0}
	total = frappe.db.count("Item", filters)
	limit_start = (page - 1) * page_size
	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["name"],
		limit_start=limit_start,
		limit_page_length=page_size,
		order_by="modified desc",
	)
	data = []
	for row in items:
		code = row.get("name")
		if not code:
			continue
		one = _item_to_raw_material_row(code, warehouse)
		if one:
			data.append(one)

	return {
		"data": data,
		"total": total,
		"page": page,
		"page_size": page_size,
	}


def _resolve_item_code(item_code: str | None = None, item_name: str | None = None) -> str:
	"""根据 item_code 或 item_name 解析到 Item.name（通常就是 item_code）。"""
	item_code = (item_code or "").strip()
	item_name = (item_name or "").strip()

	if item_code:
		return item_code

	if not item_name:
		frappe.throw("item_code or item_name is required")

	# item_name 不保证唯一；这里取最近修改的第一条，避免返回多条导致 API 复杂化
	found = frappe.get_all(
		"Item",
		filters={"item_name": item_name},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if not found:
		frappe.throw(f"Item with item_name={item_name} does not exist")

	return found[0]["name"]


@frappe.whitelist()
def _get_default_leaf_name(doctype: str) -> str:
	"""取一个可用的叶子节点主数据 name（is_group=0）。"""
	name = frappe.db.get_value(doctype, {"is_group": 0}, "name")
	if not name:
		frappe.throw(f"Missing master: {doctype}")
	return name


def _ensure_customer_exists(customer: str, create_if_missing: bool = True) -> None:
	if frappe.db.exists("Customer", customer):
		return
	if not create_if_missing:
		frappe.throw(f"Customer {customer} does not exist")

	customer_group = _get_default_leaf_name("Customer Group")
	territory = _get_default_leaf_name("Territory")

	frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer,
			"customer_group": customer_group,
			"territory": territory,
		}
	).insert(ignore_permissions=True)


def _ensure_item_exists(
	item_code: str,
	item_name: str | None = None,
	create_if_missing: bool = True,
	item_group: str | None = None,
	stock_uom: str | None = None,
) -> None:
	if frappe.db.exists("Item", item_code):
		return
	if not create_if_missing:
		frappe.throw(f"Item {item_code} does not exist")

	# 默认成品物料组；若不存在则取任意叶子节点
	if not (item_group or "").strip():
		item_group = "成品" if frappe.db.exists("Item Group", "成品") else _get_default_leaf_name("Item Group")
	else:
		item_group = (item_group or "").strip()
	stock_uom = (stock_uom or "").strip() or "Nos"
	item_name = (item_name or "").strip() or item_code

	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": item_group,
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"is_sales_item": 1,
		}
	).insert(ignore_permissions=True)


def _carton_spec_exists(length, width, height, item_group=None):
	"""检查是否已存在相同纸箱规格（长、宽、高一致）且同一物料组的 Item。"""
	l = (length or "").strip()
	w = (width or "").strip()
	h = (height or "").strip()
	if not l or not w or not h:
		return False
	filters = {
		"br_carton_length": l,
		"br_carton_width": w,
		"br_carton_height": h,
	}
	if (item_group or "").strip():
		filters["item_group"] = (item_group or "").strip()
	existing = frappe.get_all(
		"Item",
		filters=filters,
		limit_page_length=1,
	)
	return len(existing) > 0


def _parse_suppliers(suppliers):
	"""将 suppliers 转为 list（支持 JSON 字符串）。"""
	if suppliers is None:
		return []
	if isinstance(suppliers, str):
		try:
			return frappe.parse_json(suppliers) or []
		except Exception:
			return []
	return list(suppliers)


def _validate_suppliers_exist(suppliers):
	"""校验传入的供应商均在系统中存在；若有不存在则抛出，不允许添加包材。"""
	parsed = _parse_suppliers(suppliers)
	if not parsed:
		return
	invalid = []
	for row in parsed:
		supplier = (row.get("supplier") or "").strip()
		if not supplier:
			continue
		if not frappe.db.exists("Supplier", supplier):
			invalid.append(supplier)
	if invalid:
		frappe.throw(
			f"以下供应商不存在：{', '.join(invalid)}，不允许添加包材。",
			title="供应商无效",
		)


def _ensure_supplier_items(doc, suppliers=None):
	"""为 Item 写入供应商明细。suppliers 为 None 或空时，取系统中全部 Supplier 并写入（单价、是否开票默认 0）。"""
	suppliers = _parse_suppliers(suppliers)
	if not suppliers:
		# 未传则取系统中全部供应商，单价 0、是否开票 0
		names = frappe.get_all("Supplier", pluck="name", order_by="name")
		suppliers = [{"supplier": n, "custom_price": 0, "custom_isinvoice": 0} for n in names]
	item_meta = frappe.get_meta("Item Supplier")
	has_price = item_meta.get_field("custom_price") is not None
	has_isinvoice = item_meta.get_field("custom_isinvoice") is not None
	has_pricing_factor = item_meta.get_field("custom_pricing_factor") is not None
	for row in suppliers:
		supplier = (row.get("supplier") or "").strip()
		if not supplier or not frappe.db.exists("Supplier", supplier):
			continue
		entry = {"supplier": supplier}
		if row.get("supplier_part_no") is not None:
			entry["supplier_part_no"] = row.get("supplier_part_no")
		if has_price:
			entry["custom_price"] = float(row.get("custom_price") or 0)
		if has_isinvoice:
			entry["custom_isinvoice"] = 1 if row.get("custom_isinvoice") else 0
		if has_pricing_factor:
			val = row.get("custom_pricing_factor")
			entry["custom_pricing_factor"] = float(val) if val is not None and val != "" else 1.0
		doc.append("supplier_items", entry)
	if doc.get("supplier_items"):
		doc.save(ignore_permissions=True)


@frappe.whitelist()
def add_packaging_material(
	item_code: str | None = None,
	item_name: str | None = None,
	br_carton_length: str | None = None,
	br_carton_width: str | None = None,
	br_carton_height: str | None = None,
	item_group: str | None = None,
	stock_uom: str | None = None,
	suppliers: str | list | None = None,
	description: str | None = None,
	custom_weight: str | None = None,
	custom_number_of_holes: int | None = None,
	custom_pallet_material: str | None = None,
):
	"""
	添加包材：按规格（纸箱长、宽、高）创建一条包材 Item。
	若系统中已存在相同规格的纸箱，则拒绝添加并抛出错误。
	可选写入供应商明细（单价、是否开票）；不传 suppliers 时自动取系统中全部供应商写入。
	吸塑时前端传 br_carton_height="0"，重量与孔数通过 custom_weight、custom_number_of_holes 传入。

	参数:
		item_code: 可选。物料编码，不传则按规格生成（CARTON-长-宽-高）。
		item_name: 可选。物料名称，默认用 item_code 或规格描述。
		br_carton_length: 必填。纸箱长度（吸塑时为长）。
		br_carton_width: 必填。纸箱宽度（吸塑时为宽）。
		br_carton_height: 必填。纸箱高度；吸塑时前端固定传 "0"，重量由 custom_weight 表示。
		item_group: 可选。物料组。不传默认「包材」；可传包材下的子组（如「纸箱」「吸塑」等）以区分包材类型。
		stock_uom: 可选。库存单位，默认 "Nos"。
		suppliers: 可选。供应商列表，JSON 或 list，每项 {supplier, custom_price?, custom_isinvoice?, supplier_part_no?}。不传则用系统中全部供应商，单价/开票默认 0。
		description: 可选。包材 Item 的描述（Item.description）。
		custom_weight: 可选。吸塑等：重量，写入 Item.custom_weight；仅吸塑新增时前端会传。
		custom_number_of_holes: 可选。吸塑等：孔数，写入 Item.custom_number_of_holes；仅吸塑新增时前端会传。

	返回:
		{"item_code": "...", "item_name": "...", "description": "...", "supplier_items": [...], "custom_weight": ..., "custom_number_of_holes": ..., ...}

	异常:
		同一物料组下规格已存在时: frappe.ValidationError "该物料组下此纸箱规格已存在"
		传入的供应商不存在时: frappe.ValidationError "以下供应商不存在：xxx，不允许添加包材。"（标题：供应商无效）
	"""
	l = (br_carton_length or "").strip()
	w = (br_carton_width or "").strip()
	h = (br_carton_height or "").strip()
	if not l or not w or not h:
		frappe.throw("纸箱长度、纸箱宽度、纸箱高度为必填项")

	# 先确定物料组，再按「物料组 + 规格」做唯一性校验
	if not (item_group or "").strip():
		item_group = "包材" if frappe.db.exists("Item Group", "包材") else _get_default_leaf_name("Item Group")
	else:
		item_group = (item_group or "").strip()

	# 校验：同一物料组下同一规格已存在则拒绝
	if _carton_spec_exists(l, w, h, item_group):
		frappe.throw("该物料组下此纸箱规格已存在")

	# 若无 item_code，按规格生成
	if not (item_code or "").strip():
		# 生成可作 item_code 的字符串（去掉空格、统一小数点等）
		safe = lambda x: x.replace(" ", "_").replace(".", "_")
		item_code = f"CARTON-{safe(l)}-{safe(w)}-{safe(h)}"
	else:
		item_code = (item_code or "").strip()
		if frappe.db.exists("Item", item_code):
			frappe.throw(f"物料编码 {item_code} 已存在，请换一个或留空由系统生成")

	item_name = (item_name or "").strip() or f"纸箱 {l}*{w}*{h}"
	stock_uom = (stock_uom or "").strip() or "Nos"
	description_val = (description or "").strip() or ""

	# 若传入了供应商列表，必须先校验全部存在；有无效供应商则不允许添加包材
	_validate_suppliers_exist(suppliers)

	# 确认自定义字段存在
	meta = frappe.get_meta("Item")
	for f in ("br_carton_length", "br_carton_width", "br_carton_height"):
		if not meta.get_field(f):
			frappe.throw(f'Item 上未找到自定义字段 "{f}"，请先执行 migrate')

	doc_dict = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": item_group,
		"stock_uom": stock_uom,
		"is_stock_item": 1,
		"is_sales_item": 1,
		"br_carton_length": l,
		"br_carton_width": w,
		"br_carton_height": h,
	}
	if description_val:
		doc_dict["description"] = description_val
	# 吸塑等：重量、孔数写入 Item 自定义字段（前端吸塑新增时会传）
	if custom_weight is not None:
		doc_dict["custom_weight"] = str(custom_weight).strip() if isinstance(custom_weight, str) else str(custom_weight)
	if custom_number_of_holes is not None:
		try:
			doc_dict["custom_number_of_holes"] = int(custom_number_of_holes)
		except (TypeError, ValueError):
			doc_dict["custom_number_of_holes"] = 0
	# 托盘等：材质写入 Item.custom_pallet_material（前端托盘新增会传）
	if (
		custom_pallet_material is not None
		and meta.get_field("custom_pallet_material")
		and frappe.db.has_column("Item", "custom_pallet_material")
	):
		val = (custom_pallet_material or "").strip()
		doc_dict["custom_pallet_material"] = val or None
	doc = frappe.get_doc(doc_dict)
	doc.insert(ignore_permissions=True)

	# 写入供应商：不传 suppliers 时取系统中全部供应商写入（单价、是否开票默认 0）
	_ensure_supplier_items(doc, suppliers)

	out = {
		"item_code": doc.item_code,
		"item_name": doc.item_name,
		"br_carton_length": doc.br_carton_length,
		"br_carton_width": doc.br_carton_width,
		"br_carton_height": doc.br_carton_height,
		"item_group": doc.item_group,
		"stock_uom": doc.stock_uom,
		"description": getattr(doc, "description", None) or "",
		"custom_weight": getattr(doc, "custom_weight", None) or "",
		"custom_number_of_holes": getattr(doc, "custom_number_of_holes", None),
	}
	out["supplier_items"] = [
		{
			"supplier": row.supplier,
			"supplier_part_no": getattr(row, "supplier_part_no", None) or "",
			"custom_price": getattr(row, "custom_price", None),
			"custom_isinvoice": getattr(row, "custom_isinvoice", None),
			"custom_pricing_factor": getattr(row, "custom_pricing_factor", None),
		}
		for row in (doc.get("supplier_items") or [])
	]
	return out


def _update_br_quotation_item_code(quotation_number: str, item_code: str) -> list[str]:
	"""根据报价单号，将该报价单下所有版本的 item_code 回写为指定物料编码。返回已更新的 BR Quotation name 列表。"""
	quotation_number = (quotation_number or "").strip()
	if not quotation_number:
		return []
	if not frappe.db.exists("DocType", "BR Quotation"):
		return []

	names = frappe.get_all(
		"BR Quotation",
		filters={"quotation_number": quotation_number},
		pluck="name",
	)
	updated = []
	for name in names:
		try:
			doc = frappe.get_doc("BR Quotation", name)
			if doc.get("item_code") != item_code:
				doc.item_code = item_code
				doc.flags.ignore_validate_update_after_submit = True
				doc.save(ignore_permissions=True)
			updated.append(name)
		except Exception:
			# 已提交单据可能无法 save，改用 db_set 直接更新
			try:
				frappe.db.set_value("BR Quotation", name, "item_code", item_code)
				frappe.db.commit()
				updated.append(name)
			except Exception:
				pass
	return updated


@frappe.whitelist()
def add_item_target_customer(
	item_code: str | None = None,
	item_name: str | None = None,
	customer: str | None = None,
	quotation_number: str | None = None,
	create_customer_if_missing: int | bool = 1,
	create_item_if_missing: int | bool = 1,
	item_group: str | None = None,
	stock_uom: str | None = None,
):
	"""
	把某个 Item（通常是成品）加入“供给客户列表”。

	主参数:
		item_code: Item.name（物料编码）。传则按编码查找/创建；不传时需传 item_name 按名称查找
		item_name: Item.item_name（物料名称）。传 item_code 时用作新建 Item 的显示名；仅传 item_name 时按名称查找
		customer: Customer.name（供给客户，必填）

	可选参数:
		quotation_number: 报价单号。物料添加成功后，将该物料编码回写到该报价单号下所有 BR Quotation 版本的 item_code
		create_customer_if_missing: 默认 1，不存在则自动创建 Customer
		create_item_if_missing: 默认 1，不存在则自动创建 Item（成品物料组）
		item_group: create_item_if_missing=1 时可选，指定 Item Group，默认「成品」
		stock_uom: create_item_if_missing=1 时可选，默认 "Nos"

	Returns:
		{
		  "item_code": "<Item.name>",
		  "item_name": "<Item.item_name>",
		  "customer": "<Customer.name>",
		  "added": true/false,
		  "target_customers": ["CUST-001", ...],
		  "quotation_number": "<报价单号>",
		  "quotation_updated": ["name1", "name2", ...]   # 已回写 item_code 的 BR Quotation 列表
		}
	"""
	customer = (customer or "").strip()
	if not customer:
		frappe.throw("customer is required")
	quotation_number_val = (quotation_number or "").strip()

	# 先确保 Customer / Item 存在（按需自动创建）
	_ensure_customer_exists(customer, create_if_missing=bool(int(create_customer_if_missing)))

	resolved_item_code = _resolve_item_code(item_code=item_code, item_name=item_name)
	_ensure_item_exists(
		resolved_item_code,
		item_name=item_name,
		create_if_missing=bool(int(create_item_if_missing)),
		item_group=item_group,
		stock_uom=stock_uom,
	)

	item = frappe.get_doc("Item", resolved_item_code)

	# 确认自定义字段已安装（避免未 migrate 时直接报 AttributeError）
	if not frappe.get_meta("Item").get_field("br_target_customers"):
		frappe.throw('Custom field "br_target_customers" not found on Item. Did you run migrate?')

	existing = {row.customer for row in (item.get("br_target_customers") or []) if row.get("customer")}
	added = False
	if customer not in existing:
		item.append("br_target_customers", {"customer": customer})
		item.save(ignore_permissions=True)
		added = True

	target_customers = [row.customer for row in (item.get("br_target_customers") or []) if row.get("customer")]

	# 若传入报价单号，则将物料编码回写到该报价单下所有 BR Quotation 版本
	quotation_updated = []
	if quotation_number_val:
		quotation_updated = _update_br_quotation_item_code(quotation_number_val, resolved_item_code)

	out = {
		"item_code": resolved_item_code,
		"item_name": item.item_name or resolved_item_code,
		"customer": customer,
		"added": added,
		"target_customers": target_customers,
	}
	if quotation_number_val:
		out["quotation_number"] = quotation_number_val
		out["quotation_updated"] = quotation_updated
	return out


def debug_add_item_target_customer(
	customer: str = "__BR_DEBUG_CUSTOMER__",
	item_code: str = "__BR_DEBUG_FG_ITEM__",
):
	"""仅用于 bench execute 调试：确保存在 Customer/Item 后调用 add_item_target_customer。"""
	# 确保自定义字段存在
	if not frappe.get_meta("Item").get_field("br_target_customers"):
		frappe.throw('Custom field "br_target_customers" not found on Item. Did you run migrate?')

	# 准备 Customer 所需的主数据
	customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
	if not customer_group or not territory:
		frappe.throw("Missing masters: Customer Group / Territory")

	if not frappe.db.exists("Customer", customer):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer,
				"customer_group": customer_group,
				"territory": territory,
			}
		).insert(ignore_permissions=True)

	# 准备 Item 所需的主数据
	item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	if not item_group:
		frappe.throw("Missing master: Item Group")

	if not frappe.db.exists("Item", item_code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": item_group,
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"is_sales_item": 1,
			}
		).insert(ignore_permissions=True)

	return add_item_target_customer(item_code=item_code, customer=customer)
