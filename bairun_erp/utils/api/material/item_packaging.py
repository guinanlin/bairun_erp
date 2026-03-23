# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
# 包材价格页接口：按分类分页返回包材列表、供应商配置、供应商历史单价。
#
# bench execute 入口示例（站点请改为实际站点，如 site2.local）:
#   按物料组查询包材列表（物料组=纸箱）:
#     bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"item_group": "纸箱", "page": 1, "page_size": 20}'
#   按分类 key 查询:
#     bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"category": "box", "page": 1, "page_size": 20}'
#   供应商历史单价:
#     bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_supplier_price_history --kwargs '{"supplier_id": "SUP-001", "item_group": "纸箱"}'

from __future__ import unicode_literals

import frappe
from frappe.utils import getdate

from bairun_erp.utils.api.material.item import _ensure_supplier_items, _validate_suppliers_exist

# 前端分类 key -> 物料组 name（与 PACKAGING_CATEGORIES 一致）
PACKAGING_CATEGORY_MAP = {
	"box": "纸箱",
	"foam-pad": "泡沫垫板",
	"laminated-foam-pad": "覆膜泡沫垫板",
	"foam-tray": "泡沫坑盘",
	"laminated-foam-tray": "覆膜泡沫坑盘",
	"foam-edge-pad": "泡沫护边垫板",
	"laminated-foam-edge-pad": "覆膜泡沫护边垫板",
	"pe-film": "PE膜",
	"dust-free-paper": "无尘纸",
	"dust-bag": "防尘袋",
	"blister": "吸塑",
	"transparent-tape": "透明胶带",
	"red-tape": "红色胶带",
	"stretch-film": "缠绕膜",
	"pallet": "托盘",
}

# 物料组 name -> 分类 key（反向，用于返回 category）
ITEM_GROUP_TO_CATEGORY = {v: k for k, v in PACKAGING_CATEGORY_MAP.items()}


def _resolve_item_group(item_group=None, category=None):
	"""解析出物料组 name。优先 item_group，否则用 category 映射。"""
	item_group = (item_group or "").strip()
	category = (category or "").strip()
	if item_group:
		return item_group
	if category and category in PACKAGING_CATEGORY_MAP:
		return PACKAGING_CATEGORY_MAP[category]
	return None


def _float_or_none(val):
	"""转为 float，无效则 None。"""
	if val is None or val == "":
		return None
	try:
		return float(val)
	except (TypeError, ValueError):
		return None


@frappe.whitelist()
def get_packaging_material_page(
	item_group=None,
	category=None,
	page=1,
	page_size=20,
):
	"""
	按包材分类获取「包材价格页」列表数据（分页）。
	返回该分类下的供应商列表 + 规格列表（含长宽高、描述、各供应商单价与是否开票）。

	参数:
		item_group: 物料组名称，如「纸箱」「泡沫垫板」。与 category 二选一。
		category: 分类 key，如 box、foam-pad。与 item_group 二选一。
		page: 页码，从 1 开始。
		page_size: 每页条数。

	返回:
		{
		  "item_group": "纸箱",
		  "category": "box",
		  "suppliers": [{"id", "name", "unit_price", "invoice_enabled"}, ...],
		  "specs": [{"id", "length", "width", "height", "product_requirements", "work_instruction_url", "amounts": [num?, ...]}, ...],
		  "total": N,
		  "page": P,
		  "page_size": S
		}

	bench execute 示例（物料组=纸箱）:
		bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"item_group": "纸箱", "page": 1, "page_size": 20}'
	或按分类 key:
		bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"category": "box", "page": 1, "page_size": 20}'
	"""
	group_name = _resolve_item_group(item_group=item_group, category=category)
	if not group_name:
		frappe.throw("请传入 item_group 或 category（如 纸箱 / box）")

	page = max(1, int(page or 1))
	page_size = max(1, min(500, int(page_size or 20)))

	# 包材 Item：有纸箱规格字段的、且属于该物料组
	meta = frappe.get_meta("Item")
	has_carton = all(meta.get_field(f) for f in ("br_carton_length", "br_carton_width", "br_carton_height"))
	has_pallet_material = bool(meta.get_field("custom_pallet_material")) and frappe.db.has_column("Item", "custom_pallet_material")
	has_work_instruction_url = bool(meta.get_field("custom_work_instruction_url")) and frappe.db.has_column(
		"Item", "custom_work_instruction_url"
	)
	if not has_carton:
		return {
			"item_group": group_name,
			"category": ITEM_GROUP_TO_CATEGORY.get(group_name, ""),
			"suppliers": [],
			"specs": [],
			"total": 0,
			"page": page,
			"page_size": page_size,
		}

	# 只取有纸箱规格的（长非空即可视为包材规格）
	filters = {"item_group": group_name, "br_carton_length": ["!=", ""]}
	total = frappe.db.count("Item", filters=filters)
	limit_start = (page - 1) * page_size
	item_fields = ["name", "description", "br_carton_length", "br_carton_width", "br_carton_height"]
	if has_pallet_material:
		item_fields.append("custom_pallet_material")
	if has_work_instruction_url:
		item_fields.append("custom_work_instruction_url")
	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=item_fields,
		limit_start=limit_start,
		limit_page_length=page_size,
		order_by="modified desc",
	)

	item_names = [i["name"] for i in items]
	if not item_names:
		return {
			"item_group": group_name,
			"category": ITEM_GROUP_TO_CATEGORY.get(group_name, ""),
			"suppliers": [],
			"specs": [],
			"total": total,
			"page": page,
			"page_size": page_size,
		}

	# 拉取所有相关 Item Supplier（custom_price、custom_isinvoice、custom_pricing_factor 为自定义字段）
	parentfield = "supplier_items"
	rows = frappe.db.sql(
		"""
		SELECT parent, supplier, custom_price, custom_isinvoice, custom_pricing_factor
		FROM `tabItem Supplier`
		WHERE parent IN %(parents)s AND parenttype = 'Item' AND parentfield = %(parentfield)s
		ORDER BY parent, supplier
		""",
		{"parents": item_names, "parentfield": parentfield},
		as_dict=True,
	)

	# 按 item 汇总 supplier -> (custom_price, custom_isinvoice, custom_pricing_factor)
	item_suppliers = {}
	for r in rows:
		parent = r.get("parent")
		if not parent:
			continue
		if parent not in item_suppliers:
			item_suppliers[parent] = {}
		sid = r.get("supplier")
		if sid:
			pf = _float_or_none(r.get("custom_pricing_factor"))
			if pf is None:
				pf = 1.0
			item_suppliers[parent][sid] = (
				_float_or_none(r.get("custom_price")),
				1 if r.get("custom_isinvoice") else 0,
				pf,
			)

	# 全部分类下出现过的供应商，去重并固定顺序
	all_supplier_ids = sorted({r.get("supplier") for r in rows if r.get("supplier")})
	supplier_names = {}
	if all_supplier_ids:
		for s in frappe.get_all("Supplier", filters={"name": ["in", all_supplier_ids]}, fields=["name", "supplier_name"]):
			supplier_names[s["name"]] = s.get("supplier_name") or s["name"]

	suppliers = []
	for sid in all_supplier_ids:
		unit_price = None
		invoice_enabled = False
		pricing_factor = 1.0
		for item_name, sups in item_suppliers.items():
			if sid in sups:
				tup = sups[sid]
				price = tup[0]
				inv = tup[1]
				pf = tup[2] if len(tup) > 2 else 1.0
				if unit_price is None:
					unit_price = price
				invoice_enabled = bool(inv)
				pricing_factor = pf
				break
		suppliers.append({
			"id": sid,
			"name": supplier_names.get(sid, sid),
			"unit_price": unit_price,
			"invoice_enabled": invoice_enabled,
			"custom_pricing_factor": pricing_factor,
		})

	# 规格行：与 suppliers 顺序一致的 amounts
	specs = []
	for i in items:
		item_code = i.get("name")
		sup_map = item_suppliers.get(item_code, {})
		amounts = [sup_map.get(s["id"], (None, False, 1.0))[0] for s in suppliers]
		row = {
			"id": item_code,
			"length": _float_or_none(i.get("br_carton_length")),
			"width": _float_or_none(i.get("br_carton_width")),
			"height": _float_or_none(i.get("br_carton_height")),
			"material": (i.get("custom_pallet_material") or None) if has_pallet_material else None,
			"product_requirements": (i.get("description") or "").strip(),
			"amounts": amounts,
		}
		if has_work_instruction_url:
			wiu = (i.get("custom_work_instruction_url") or "").strip()
			row["work_instruction_url"] = wiu if wiu else None
		else:
			row["work_instruction_url"] = None
		specs.append(row)

	return {
		"item_group": group_name,
		"category": ITEM_GROUP_TO_CATEGORY.get(group_name, ""),
		"suppliers": suppliers,
		"specs": specs,
		"total": total,
		"page": page,
		"page_size": page_size,
	}


@frappe.whitelist()
def get_supplier_price_history(
	supplier_id,
	item_group=None,
	item_code=None,
):
	"""
	获取某供应商的历史单价记录（用于「查看历史单价」弹窗）。
	当前以 Item Supplier 行作为记录，effective_date 取 creation。

	参数:
		supplier_id: 供应商主键（Supplier.name）。
		item_group: 可选，只返回该物料组下的规格。
		item_code: 可选，只返回该物料。

	返回:
		{
		  "supplier_id": "...",
		  "supplier_name": "...",
		  "history": [{"unit_price", "effective_date", "item_code"}, ...]
		}
	"""
	supplier_id = (supplier_id or "").strip()
	if not supplier_id:
		frappe.throw("请传入 supplier_id")
	if not frappe.db.exists("Supplier", supplier_id):
		frappe.throw("供应商不存在", title="供应商无效")

	supplier_name = frappe.db.get_value("Supplier", supplier_id, "supplier_name") or supplier_id

	conditions = ["supplier = %(supplier_id)s", "parenttype = 'Item'", "parentfield = 'supplier_items'"]
	params = {"supplier_id": supplier_id}
	if (item_code or "").strip():
		conditions.append("parent = %(item_code)s")
		params["item_code"] = (item_code or "").strip()
	if (item_group or "").strip():
		conditions.append("""
			parent IN (SELECT name FROM tabItem WHERE item_group = %(item_group)s)
		""")
		params["item_group"] = (item_group or "").strip()

	sql = """
		SELECT s.parent AS item_code, s.custom_price AS unit_price, s.creation
		FROM `tabItem Supplier` s
		WHERE {conditions}
		ORDER BY s.creation DESC
	""".format(conditions=" AND ".join(conditions))
	rows = frappe.db.sql(sql, params, as_dict=True)

	history = []
	for r in rows:
		history.append({
			"unit_price": _float_or_none(r.get("unit_price")),
			"effective_date": getdate(r.get("creation")).strftime("%Y-%m-%d") if r.get("creation") else "",
			"item_code": r.get("item_code") or "",
		})

	return {
		"supplier_id": supplier_id,
		"supplier_name": supplier_name,
		"history": history,
	}


@frappe.whitelist()
def apply_supplier_prices_by_item_group(
	item_group=None,
	category=None,
	suppliers=None,
):
	"""
	按物料组批量应用供应商价格：将该物料组下所有包材 Item 的供应商明细统一为入参 suppliers 配置。
	用于包材价格页「供应商价格审核」按钮：页顶配置的供应商+单价+开票一次性应用到该组下全部规格。

	参数:
		item_group: 物料组名称，如「纸箱」「泡沫垫板」。与 category 二选一。
		category: 分类 key，如 box、foam-pad。与 item_group 二选一，会映射为物料组。
		suppliers: 供应商配置列表（建议最多 3 条，与页顶槽位一致）。每项含 supplier（必填）、custom_price、custom_isinvoice、supplier_part_no。

	返回:
		{"success": true, "message": "...", "updated_count": N, "item_group": "..."}

	bench execute 示例:
		bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.apply_supplier_prices_by_item_group --kwargs '{"item_group": "纸箱", "suppliers": [{"supplier": "SUP-001", "custom_price": 2.23, "custom_isinvoice": 1}, {"supplier": "SUP-0011", "custom_price": 1.88, "custom_isinvoice": 0}]}'
	"""
	group_name = _resolve_item_group(item_group=item_group, category=category)
	if not group_name:
		return {
			"success": False,
			"message": "请传入 item_group 或 category（如 纸箱 / box）",
			"updated_count": 0,
			"item_group": "",
		}

	if not suppliers:
		return {
			"success": False,
			"message": "请传入 suppliers 供应商配置列表",
			"updated_count": 0,
			"item_group": group_name,
		}

	# 校验供应商均存在
	try:
		_validate_suppliers_exist(suppliers)
	except frappe.ValidationError as e:
		return {
			"success": False,
			"message": str(e),
			"updated_count": 0,
			"item_group": group_name,
		}

	# 该物料组下所有有纸箱规格的 Item（与 get_packaging_material_page 一致）
	filters = {"item_group": group_name, "br_carton_length": ["!=", ""]}
	item_names = frappe.get_all(
		"Item",
		filters=filters,
		pluck="name",
	)
	if not item_names:
		return {
			"success": True,
			"message": f"物料组「{group_name}」下暂无包材规格，无需更新",
			"updated_count": 0,
			"item_group": group_name,
		}

	updated_count = 0
	for item_code in item_names:
		try:
			doc = frappe.get_doc("Item", item_code)
			doc.supplier_items = []
			_ensure_supplier_items(doc, suppliers)
			updated_count += 1
		except Exception as e:
			frappe.log_error(title=f"apply_supplier_prices_by_item_group {item_code}", message=str(e))
			return {
				"success": False,
				"message": f"更新物料 {item_code} 时失败：{e}",
				"updated_count": updated_count,
				"item_group": group_name,
			}

	return {
		"success": True,
		"message": f"已将该物料组下 {updated_count} 个规格的供应商价格统一为当前配置",
		"updated_count": updated_count,
		"item_group": group_name,
	}
