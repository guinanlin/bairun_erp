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
from bairun_erp.utils.api.material.item_attrs_apply import (
	apply_item_attrs,
	apply_item_warehouse,
	resolve_warehouse_name,
)


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


def _parse_kwargs_json_data(kwargs):
	"""兼容 /api/method 直传参数与 json_data 包装参数。"""
	jd = kwargs.get("json_data")
	if jd is None:
		jd = {k: v for k, v in kwargs.items() if k not in ("cmd",)}
	if isinstance(jd, str):
		try:
			jd = json.loads(jd)
		except (TypeError, ValueError):
			jd = {}
	if not isinstance(jd, dict):
		jd = {}
	return jd


def _pick(data, *keys):
	for k in keys:
		if k in data:
			return data.get(k)
	return None


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
	# Stock Settings 为 Naming Series 时，insert 后实际 name/item_code 由系列生成，必须使用 doc.name
	return doc.name


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
				apply_item_attrs(item_doc, item_attrs)
			if warehouse:
				company = _get_default_company()
				apply_item_warehouse(item_doc, warehouse, company)
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
	parent_source_wh = resolve_warehouse_name(parent_wh, company) if parent_wh else ""
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
		source_warehouse = (resolve_warehouse_name(wh_input, company) if wh_input else None) or parent_source_wh
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


def _build_target_bom_items_from_tree(tree, company):
	"""
	基于当前画布根节点 children 生成目标 BOM items，并收集非法节点。
	要求字段：item_code/item_name/bom_qty，warehouse/supplier 可选。
	"""
	children = tree.get("children") or []
	items = []
	failed_items = []

	for child in children:
		node_id = child.get("id") or ""
		item_code = (child.get("item_code") or "").strip()
		item_name = (child.get("item_name") or "").strip()
		if not item_code:
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "item_code 不能为空",
			})
			continue
		if not item_name:
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "item_name 不能为空",
			})
			continue

		qty = child.get("bom_qty")
		if qty in (None, ""):
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "bom_qty 不能为空",
			})
			continue
		try:
			qty = float(qty)
		except Exception:
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "bom_qty 必须为数字",
			})
			continue
		if qty <= 0:
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "bom_qty 必须大于 0",
			})
			continue

		if not frappe.db.exists("Item", item_code):
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "物料不存在",
			})
			continue

		warehouse = (child.get("warehouse") or (child.get("item_attrs") or {}).get("warehouse") or "").strip()
		source_warehouse = resolve_warehouse_name(warehouse, company) if warehouse else None
		if warehouse and not source_warehouse:
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "warehouse 无法匹配到系统仓库",
			})
			continue

		supplier = (child.get("supplier") or "").strip()
		if supplier and (not frappe.db.exists("Supplier", supplier)):
			failed_items.append({
				"node_id": node_id,
				"item_code": item_code,
				"error": "supplier 不存在",
			})
			continue

		uom = frappe.get_cached_value("Item", item_code, "stock_uom") or "Nos"
		row = {
			"node_id": node_id,
			"item_code": item_code,
			"item_name": item_name,
			"qty": qty,
			"uom": uom,
			"source_warehouse": source_warehouse,
			"supplier": supplier or None,
		}
		items.append(row)

	return items, failed_items


def _apply_bom_item_row_fields(row_doc, item_row):
	"""仅写入 BOM Item 存在的字段，避免自定义字段差异导致报错。"""
	row_doc.item_code = item_row["item_code"]
	row_doc.qty = item_row["qty"]
	if hasattr(row_doc, "uom"):
		row_doc.uom = item_row.get("uom")
	if hasattr(row_doc, "stock_uom"):
		row_doc.stock_uom = item_row.get("uom")

	meta = frappe.get_meta("BOM Item")
	if meta.has_field("source_warehouse"):
		row_doc.source_warehouse = item_row.get("source_warehouse")
	if meta.has_field("supplier"):
		row_doc.supplier = item_row.get("supplier")


def _update_bom_items_merge(bom_doc, new_items):
	"""
	merge 策略：按 item_code 合并。
	- 命中则更新（默认命中首条）
	- 未命中则新增
	- 不删除未传入行
	"""
	updated = 0
	created = 0

	existing_map = {}
	for row in (bom_doc.items or []):
		code = (row.item_code or "").strip()
		if code and code not in existing_map:
			existing_map[code] = row

	for item_row in new_items:
		target = existing_map.get(item_row["item_code"])
		if target:
			_apply_bom_item_row_fields(target, item_row)
			updated += 1
		else:
			new_row = bom_doc.append("items", {})
			_apply_bom_item_row_fields(new_row, item_row)
			created += 1

	return updated, created, 0


def _update_bom_items_replace(bom_doc, new_items):
	"""replace 策略：按画布全量替换 BOM items。"""
	removed = len(bom_doc.items or [])
	bom_doc.set("items", [])
	for item_row in new_items:
		new_row = bom_doc.append("items", {})
		_apply_bom_item_row_fields(new_row, item_row)
	return 0, len(new_items), removed


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


@frappe.whitelist(allow_guest=False)
def update_bom_from_canvas_tree(**kwargs):
	"""
	更新指定 BOM 子项。
	入参支持 json_data:
	{
	  "bom_name": "BOM-XXX",
	  "tree_data": "{...}",
	  "update_mode": "merge|replace"
	}
	"""
	jd = _parse_kwargs_json_data(kwargs)
	bom_name = (_pick(jd, "bom_name", "bomName") or "").strip()
	tree_data = _pick(jd, "tree_data", "treeData")
	update_mode = ((_pick(jd, "update_mode", "updateMode") or "merge")).strip().lower()

	if not bom_name:
		return {"success": False, "message": "bom_name 不能为空", "data": {"bom_name": "", "failed_items": []}}
	if update_mode not in ("merge", "replace"):
		return {
			"success": False,
			"message": "update_mode 仅支持 merge 或 replace",
			"data": {"bom_name": bom_name, "failed_items": []},
		}
	if not frappe.db.exists("BOM", bom_name):
		return {
			"success": False,
			"message": "BOM不存在或无权限更新",
			"data": {"bom_name": bom_name, "failed_items": []},
		}

	try:
		tree = _parse_tree(tree_data)
		bom_doc = frappe.get_doc("BOM", bom_name)
		frappe.has_permission("BOM", doc=bom_doc, ptype="write", throw=True)

		company = bom_doc.company or _get_default_company()
		new_items, failed_items = _build_target_bom_items_from_tree(tree, company)
		if failed_items:
			return {
				"success": False,
				"message": "存在非法节点，请修正后重试",
				"data": {
					"bom_name": bom_name,
					"updated_items_count": 0,
					"created_items_count": 0,
					"removed_items_count": 0,
					"failed_items": failed_items,
				},
			}

		frappe.db.savepoint("update_bom_from_canvas_tree")
		if update_mode == "replace":
			updated_count, created_count, removed_count = _update_bom_items_replace(bom_doc, new_items)
		else:
			updated_count, created_count, removed_count = _update_bom_items_merge(bom_doc, new_items)

		# 保留简要审计记录
		bom_doc.add_comment(
			"Edit",
			"Canvas BOM update by {0}: mode={1}, updated={2}, created={3}, removed={4}".format(
				frappe.session.user, update_mode, updated_count, created_count, removed_count
			),
		)
		bom_doc.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"success": True,
			"message": "更新成功",
			"data": {
				"bom_name": bom_name,
				"updated_items_count": updated_count,
				"created_items_count": created_count,
				"removed_items_count": removed_count,
				"failed_items": [],
			},
		}
	except frappe.PermissionError:
		return {
			"success": False,
			"message": "BOM不存在或无权限更新",
			"data": {"bom_name": bom_name, "failed_items": []},
		}
	except Exception as e:
		frappe.db.rollback(save_point="update_bom_from_canvas_tree")
		frappe.log_error(
			title="update_bom_from_canvas_tree",
			message=frappe.get_traceback(),
		)
		return {
			"success": False,
			"message": str(e),
			"data": {"bom_name": bom_name, "failed_items": []},
		}
