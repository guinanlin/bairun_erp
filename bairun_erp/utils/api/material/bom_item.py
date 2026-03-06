# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
# 画布 BOM 树生成：从画布 JSON 树创建 Item 与 BOM。
#
# bench execute 示例（站点请改为实际站点）:
#   bench --site site2.local execute bairun_erp.utils.api.material.bom_item.create_bom_from_canvas_tree --kwargs '{"tree_data": "{\"id\":\"root\",\"item_name\":\"27-01\",\"children\":[]}"}'

from __future__ import unicode_literals

import json
import frappe

from bairun_erp.utils.api.items import save_item_bom_structure
from bairun_erp.utils.api.material.item import _get_default_leaf_name


# --- 工具层 ---


def _parse_tree(tree_data):
	"""解析并校验 tree_data，返回 dict。"""
	if not tree_data:
		frappe.throw("tree_data is required")
	if isinstance(tree_data, str):
		try:
			tree_data = json.loads(tree_data)
		except json.JSONDecodeError as e:
			frappe.throw("Invalid JSON format for tree_data: {0}".format(str(e)))
	if not isinstance(tree_data, dict):
		frappe.throw("tree_data must be a dict or JSON string")
	item_name = (tree_data.get("item_name") or "").strip()
	if not item_name:
		frappe.throw("tree_data must have item_name at root")
	return tree_data


def _collect_nodes_depth_first(node, acc=None):
	"""深度优先递归收集所有节点（含 node 自身）到 acc。"""
	if acc is None:
		acc = []
	acc.append(node)
	children = node.get("children") or []
	for child in children:
		_collect_nodes_depth_first(child, acc)
	return acc


def _get_default_company():
	"""获取默认公司。"""
	company = frappe.defaults.get_user_default("Company")
	if company:
		return company
	companies = frappe.get_all("Company", limit=1)
	if not companies:
		frappe.throw("No Company found")
	return companies[0]["name"]


def _get_bom_nodes_bottom_up(nodes):
	"""
	从节点列表中筛选出有 children 的节点，并按自底向上顺序排列。
	深度优先收集时，子节点在父节点之后，反转后即自底向上。
	"""
	with_children = [n for n in nodes if n.get("children")]
	return list(reversed(with_children))


# --- 第一步：Item 创建/验证 ---

# Item 主表可从 item_attrs 写入的字段
_ITEM_ATTRS_MAIN_FIELDS = (
	"br_packing_qty",
	"br_turnover",
	"br_carton_spec",
	"br_volume",
	"br_carton_length",
	"br_carton_width",
	"br_carton_height",
	"br_supplier",
	"br_price",
)

# 子表字段名
_ITEM_ATTRS_CHILD_TABLES = ("br_process_suppliers", "br_packaging_details", "br_pallet_selections")


def _apply_item_attrs(item_doc, item_attrs):
	"""
	将 item_attrs 中的主表字段和子表数据应用到 Item 文档。
	不执行 save，由调用方负责。
	"""
	if not item_attrs or not isinstance(item_attrs, dict):
		return

	# 主表字段：非 null 且非空字符串时写入
	for k in _ITEM_ATTRS_MAIN_FIELDS:
		if k not in item_attrs:
			continue
		val = item_attrs[k]
		if val is not None and (not isinstance(val, str) or val.strip() != ""):
			item_doc.set(k, val)

	# 子表：清空后按数组顺序 append（子表行字段可为 null，Frappe 会正常处理）
	for child_field in _ITEM_ATTRS_CHILD_TABLES:
		if child_field not in item_attrs:
			continue
		rows = item_attrs[child_field]
		if not isinstance(rows, (list, tuple)):
			continue
		item_doc.set(child_field, [])
		for row in rows:
			if isinstance(row, dict):
				item_doc.append(child_field, row)


def _resolve_warehouse_name(warehouse_input, company):
	"""
	解析仓库名称。ERPNext 仓库 name 通常带公司后缀（如 半成品 - B），
	前端可能传「半成品仓库」「半成品」等。优先精确匹配，否则按 name 或 warehouse_name 匹配。
	返回实际 Warehouse name 或 None。
	"""
	if not warehouse_input or not isinstance(warehouse_input, str) or not warehouse_input.strip():
		return None
	wh = warehouse_input.strip()
	candidates = [wh]
	# 带公司后缀的 name
	abbr = frappe.get_cached_value("Company", company, "abbr") or ""
	if abbr:
		candidates.append("{0} - {1}".format(wh, abbr))
	# 「半成品仓库」→「半成品」常见简写回退
	if wh.endswith("仓库") and len(wh) > 2:
		candidates.append(wh[:-2])
		if abbr:
			candidates.append("{0} - {1}".format(wh[:-2], abbr))
	for c in candidates:
		if frappe.db.exists("Warehouse", c):
			return c
	# 按 warehouse_name 匹配该公司下叶子仓库
	found = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": wh, "company": company, "is_group": 0},
		"name",
	)
	if found:
		return found
	if wh.endswith("仓库") and len(wh) > 2:
		found = frappe.db.get_value(
			"Warehouse",
			{"warehouse_name": wh[:-2], "company": company, "is_group": 0},
			"name",
		)
	return found


def _apply_item_warehouse(item_doc, warehouse, company):
	"""
	将仓库写入 Item 的 item_defaults 子表（按公司设置 default_warehouse）。
	不执行 save，由调用方负责。
	"""
	if not warehouse or not isinstance(warehouse, str) or not warehouse.strip():
		return
	wh = _resolve_warehouse_name(warehouse, company)
	if not wh:
		return
	# 查找是否已有该公司记录，有则更新，无则追加
	defaults = item_doc.get("item_defaults") or []
	found = False
	for d in defaults:
		if d.get("company") == company:
			d.default_warehouse = wh
			found = True
			break
	if not found:
		item_doc.append("item_defaults", {"company": company, "default_warehouse": wh})


def _create_item_from_node(node):
	"""
	根据节点创建 Item。item_code 为空时用 item_name；item_group 默认「成品」或叶子节点。
	插入前二次检查：若物料已存在则直接返回，不执行 INSERT，避免 1062 主键重复。
	返回 item_code。
	"""
	item_name = (node.get("item_name") or "").strip()
	if not item_name:
		frappe.throw("item_name is required")
	item_code = (node.get("item_code") or "").strip() or item_name
	# 插入前二次检查，防止并发或上层检查遗漏导致 1062
	if frappe.db.exists("Item", item_code):
		return item_code
	item_group = (node.get("item_group") or "").strip()
	if not item_group:
		item_group = "成品" if frappe.db.exists("Item Group", "成品") else _get_default_leaf_name("Item Group")
	stock_uom = "Nos"
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": item_group,
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"is_sales_item": 1,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except Exception as e:
		err_str = str(e).lower()
		# 1062 主键重复：物料已存在，视为成功复用，不抛错
		if ("1062" in err_str or "duplicate entry" in err_str) and frappe.db.exists("Item", item_code):
			return item_code
		raise
	return item_code


def _ensure_or_validate_item(node):
	"""
	创建或验证单个节点对应的 Item，并在存在 item_attrs 时写入物料属性。
	先查后建：若物料已存在则直接复用，不执行 INSERT，避免 1062 主键重复错误。
	返回: { node_id, item_code, item_name, status, error? }
	"""
	node_id = node.get("id") or ""
	item_name = (node.get("item_name") or "").strip()
	item_code = (node.get("item_code") or "").strip() or item_name

	if not item_code:
		return {
			"node_id": node_id,
			"item_code": "",
			"item_name": item_name,
			"status": "failed",
			"error": "item_name is required",
		}

	# 先查：物料已存在则直接复用，不创建（避免 1062 主键重复）
	existed = frappe.db.exists("Item", item_code)
	if existed:
		status = "existed"
		res_code = item_code
	else:
		# 后建：物料不存在，执行创建
		try:
			res_code = _create_item_from_node(node)
			status = "created"
		except Exception as e:
			return {
				"node_id": node_id,
				"item_code": "",
				"item_name": item_name,
				"status": "failed",
				"error": str(e),
			}

	# 若有 item_attrs 或 warehouse，应用到 Item 并保存
	item_attrs = node.get("item_attrs")
	warehouse = (node.get("warehouse") or ((item_attrs or {}).get("warehouse")) or "").strip()
	if (item_attrs and isinstance(item_attrs, dict)) or warehouse:
		try:
			item_doc = frappe.get_doc("Item", res_code)
			if item_attrs and isinstance(item_attrs, dict):
				_apply_item_attrs(item_doc, item_attrs)
			if warehouse:
				company = _get_default_company()
				_apply_item_warehouse(item_doc, warehouse, company)
			item_doc.save(ignore_permissions=True)
		except Exception as e:
			return {
				"node_id": node_id,
				"item_code": res_code,
				"item_name": item_name or res_code,
				"status": "failed",
				"error": "item_attrs/warehouse apply failed: {0}".format(str(e)),
			}

	return {
		"node_id": node_id,
		"item_code": res_code,
		"item_name": item_name or res_code,
		"status": status,
	}


def _run_step1_ensure_items(tree):
	"""
	第一步：遍历所有节点，创建或验证 Item。
	返回: (items: list, step1_complete: bool, node_map: dict)
	node_map: node_id -> { item_code, item_name } 供第二步使用。
	"""
	nodes = _collect_nodes_depth_first(tree)
	node_map = {}
	items_result = []
	step1_complete = True

	for node in nodes:
		res = _ensure_or_validate_item(node)
		items_result.append(res)
		if res["status"] == "failed":
			step1_complete = False
		else:
			node_id = res["node_id"]
			node_map[node_id] = {
				"item_code": res["item_code"],
				"item_name": res.get("item_name") or res["item_code"],
			}

	return items_result, step1_complete, node_map


# --- 第二步：BOM 创建 ---


def _build_bom_data_for_node(node, node_map, company):
	"""
	构建 save_item_bom_structure 所需的 bom_data。
	node_map: node_id -> { item_code, item_name }
	"""
	node_id = node.get("id") or ""
	parent_info = node_map.get(node_id, {})
	parent_item_code = parent_info.get("item_code")
	if not parent_item_code:
		frappe.throw("Missing item_code for node {0}".format(node_id))

	currency = frappe.get_cached_value("Company", company, "default_currency") or "USD"
	children = node.get("children") or []
	# 父节点 warehouse 作为子件缺省 source_warehouse（子节点未指定时使用）
	parent_wh = (node.get("warehouse") or (node.get("item_attrs") or {}).get("warehouse") or "").strip()
	parent_source_wh = _resolve_warehouse_name(parent_wh, company) if parent_wh else ""
	bom_items = []
	for child in children:
		child_id = child.get("id") or ""
		child_info = node_map.get(child_id, {})
		child_item_code = child_info.get("item_code")
		if not child_item_code:
			continue
		qty = float(child.get("bom_qty") or 1)
		uom = frappe.get_cached_value("Item", child_item_code, "stock_uom") or "Nos"
		# 子节点 warehouse 优先，否则用父节点 warehouse，写入 BOM Item 的 source_warehouse
		wh_input = (child.get("warehouse") or (child.get("item_attrs") or {}).get("warehouse") or "").strip()
		source_warehouse = (_resolve_warehouse_name(wh_input, company) if wh_input else None) or parent_source_wh
		row = {"item_code": child_item_code, "qty": qty, "uom": uom}
		if source_warehouse:
			row["source_warehouse"] = source_warehouse
		bom_items.append(row)

	bom_data = {
		"doctype": "BOM",
		"item": parent_item_code,
		"company": company,
		"quantity": 1,
		"currency": currency,
		"items": bom_items,
	}
	return bom_data


def _run_step2_create_boms(tree, node_map):
	"""
	第二步：自底向上为有 children 的节点创建 BOM。
	单个失败不中断，全部结果返回。
	返回: (boms: list, step2_complete: bool)
	"""
	nodes = _collect_nodes_depth_first(tree)
	bom_nodes = _get_bom_nodes_bottom_up(nodes)
	company = _get_default_company()
	boms_result = []
	step2_complete = True

	for node in bom_nodes:
		node_id = node.get("id") or ""
		parent_info = node_map.get(node_id, {})
		parent_item_code = parent_info.get("item_code", "")
		try:
			bom_data = _build_bom_data_for_node(node, node_map, company)
			res = save_item_bom_structure(bom_data)
			if res.get("error"):
				boms_result.append({
					"parent_item_code": parent_item_code,
					"bom_no": None,
					"status": "failed",
					"error": res.get("error", ""),
				})
				step2_complete = False
			else:
				bom_no = res.get("bom_name") or res.get("name")
				boms_result.append({
					"parent_item_code": parent_item_code,
					"bom_no": bom_no,
					"status": "success",
				})
		except Exception as e:
			boms_result.append({
				"parent_item_code": parent_item_code,
				"bom_no": None,
				"status": "failed",
				"error": str(e),
			})
			step2_complete = False
			frappe.log_error(
				title="create_bom_from_canvas_tree BOM failed",
				message="parent_item={0}, error={1}".format(parent_item_code, str(e)),
			)

	return boms_result, step2_complete


# --- 主入口 ---


@frappe.whitelist()
def create_bom_from_canvas_tree(tree_data):
	"""
	从画布 JSON 树创建完整 BOM 体系。

	流程：
	1. 第一步：所有节点对应的 Item 先创建或验证
	2. 第二步：仅当第一步全部成功时，自底向上为有子节点的节点创建 BOM

	参数:
		tree_data: 画布 JSON 树（dict 或 JSON 字符串）

	返回:
		{
		  "items": [{"node_id", "item_code", "item_name", "status", "error?"}, ...],
		  "boms": [{"parent_item_code", "bom_no", "status", "error?"}, ...],
		  "step1_complete": bool,
		  "step2_complete": bool
		}

	bench execute 示例:
		bench --site site2.local execute bairun_erp.utils.api.material.bom_item.create_bom_from_canvas_tree --kwargs '{"tree_data": "<JSON>"}'
	"""
	tree = _parse_tree(tree_data)
	items_result, step1_complete, node_map = _run_step1_ensure_items(tree)

	boms_result = []
	step2_complete = False
	if step1_complete:
		boms_result, step2_complete = _run_step2_create_boms(tree, node_map)

	return {
		"items": items_result,
		"boms": boms_result,
		"step1_complete": step1_complete,
		"step2_complete": step2_complete,
	}
