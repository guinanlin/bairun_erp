# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
产品物料清单 API：基于销售订单明细行，展开完整 BOM 层级结构。

- get_product_bom_list: 获取单条产品物料清单详情（header + 扁平 items，含 level、bomCode）
- list_bom_material_report: BOM 物料清单报表列表（数据源：BR SO BOM List 主表，一行 = 一订单 + 一成品）

bench execute 示例:
    bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list --kwargs '{"sales_order_name": "SAL-ORD-2026-00003", "item_code": "配件_mm4io3o2ua3k"}'
    bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report --kwargs '{"json_data": {"date_from": "2025-01-01", "date_to": "2026-12-31", "page_number": 1, "page_size": 20}}'
"""

from __future__ import unicode_literals

import json
import math

import frappe
from frappe.utils import cint, flt, getdate

from bairun_erp.utils.api.material.bom_query import _build_bom_tree, _get_item_tree_fields


def _get_bom_for_item(item_code, so_item_bom_no=None):
    """
    获取 Item 的 BOM：优先 SO Item.bom_no，其次 Item.default_bom，最后查找 BOM。
    """
    if so_item_bom_no and frappe.db.exists("BOM", so_item_bom_no):
        return so_item_bom_no

    item = frappe.db.get_value(
        "Item",
        item_code,
        ["default_bom"],
        as_dict=True,
    )
    if item and item.get("default_bom") and frappe.db.exists("BOM", item.default_bom):
        return item.default_bom

    bom_name = frappe.db.get_value(
        "BOM",
        {"item": item_code, "is_active": 1, "docstatus": 1},
        "name",
        order_by="is_default desc, modified desc",
    )
    return bom_name


def _flatten_bom_tree_with_root(node, level, parent_bom_code, flat, path_ratio, so_item, include_root=False):
    """
    前序遍历 BOM 树，扁平化。若 include_root，先输出根节点；然后 children 为 level+1...
    path_ratio: 从 SO Item 到当前节点的累计配比乘积。
    so_item: 当前分支对应的 SO Item 行，用于 order_qty。
    """
    if include_root:
        flat.append({
            "node": node,
            "level": level,
            "bom_code": parent_bom_code or "A1",
            "path_ratio": path_ratio,
            "so_item": so_item,
        })
        child_level = level + 1
        child_parent_code = parent_bom_code or "A1"
    else:
        child_level = level
        child_parent_code = parent_bom_code

    children = node.get("children") or []
    base_ratio = path_ratio

    for idx, child in enumerate(children):
        child_bom_code = (child_parent_code + "-" if child_parent_code else "A") + str(idx + 1)
        child_qty = flt(child.get("bom_qty") or 1)
        child_path_ratio = base_ratio * child_qty

        flat.append({
            "node": child,
            "level": child_level,
            "bom_code": child_bom_code,
            "path_ratio": child_path_ratio,
            "so_item": so_item,
        })

        _flatten_bom_tree_with_root(child, child_level + 1, child_bom_code, flat, child_path_ratio, so_item, include_root=False)


def _get_warehouse_name(warehouse_code):
    """获取仓库名称，无则返回空"""
    if not warehouse_code:
        return ""
    return frappe.db.get_value("Warehouse", warehouse_code, "warehouse_name") or ""


def _get_supplier_name(supplier_code):
    """获取供应商名称"""
    if not supplier_code:
        return ""
    return frappe.db.get_value("Supplier", supplier_code, "supplier_name") or ""


def _get_item_group_parent_map(item_group_names):
    """
    批量获取物料组的父级。Item Group 的 parent_item_group 即父物料组名称。
    返回: dict, item_group_name -> parent_item_group (空字符串表示无父级或不存在)
    """
    if not item_group_names:
        return {}
    names = list(set(n for n in item_group_names if (n or "").strip()))
    if not names:
        return {}
    rows = frappe.get_all(
        "Item Group",
        filters={"name": ["in", names]},
        fields=["name", "parent_item_group"],
    )
    out = {}
    for r in rows:
        name = (r.get("name") or "").strip()
        parent = (r.get("parent_item_group") or "").strip()
        out[name] = parent
    return out


def _get_bin_actual_qty(item_code, warehouse):
    """获取 Bin 的 actual_qty"""
    if not item_code or not warehouse:
        return None
    val = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
    return flt(val) if val is not None else None


def _get_item_default_warehouse(item_code, company):
    """
    从 Item Default 子表获取物料的默认仓库（按公司）。
    返回: warehouse 名称或空字符串
    """
    if not (item_code and company):
        return ""
    val = frappe.db.get_value(
        "Item Default",
        {"parent": item_code, "company": company},
        "default_warehouse",
    )
    return (val or "").strip()


def _get_finished_product_warehouse_and_stock(so_doc, first_so_item):
    """
    获取成品（BOM 顶层 / 首行 SO Item）的仓库与库存。
    BOM Header 无仓库字段，成品仓库应来自 Item Default；SO 行仓库多用于出库/交货，不代表成品存放仓。
    取数规则：
    1. Item Default.default_warehouse（成品物料按公司的默认仓库，与 Bin 库存一致）
    2. SO Item.warehouse（销售订单行指定仓库）
    3. SO.set_warehouse（订单级默认仓库）
    返回: (warehouse_code, warehouse_name, inventory_qty)
    """
    warehouse_code = ""
    warehouse_name = ""
    inventory_qty = None

    if not first_so_item:
        return (warehouse_code, warehouse_name, inventory_qty)

    item_code = (first_so_item.get("item_code") or "").strip()
    if not item_code:
        return (warehouse_code, warehouse_name, inventory_qty)

    # 1. Item Default（成品存放仓，BOM Header 无仓库字段，以此为准）
    if hasattr(so_doc, "company") and so_doc.company:
        warehouse_code = _get_item_default_warehouse(item_code, so_doc.company)
    # 2. SO Item.warehouse
    if not warehouse_code:
        warehouse_code = (first_so_item.get("warehouse") or "").strip()
    # 3. SO.set_warehouse
    if not warehouse_code and hasattr(so_doc, "set_warehouse") and so_doc.set_warehouse:
        warehouse_code = (so_doc.set_warehouse or "").strip()

    if warehouse_code:
        warehouse_name = _get_warehouse_name(warehouse_code)
        inventory_qty = _get_bin_actual_qty(item_code, warehouse_code)

    return (warehouse_code or "", warehouse_name, inventory_qty)


def _so_item_to_root_node(so_item):
    """将 SO Item 转为树节点格式（用于无 BOM 的叶子项）"""
    item_code = (so_item.get("item_code") or "").strip()
    item_name = (so_item.get("item_name") or "").strip() or item_code
    return {
        "id": so_item.get("name") or "",
        "item_code": item_code,
        "item_name": item_name,
        "bom_qty": 1,
        "children": [],
    }


def _build_header(so_doc, so_items):
    """构建 header。so_items 为 SO Detail 列表，itemName 取首行，orderQty 为合计"""
    status_map = {"Draft": "draft", "Submitted": "approved", "To Bill": "approved", "Closed": "approved"}
    status = (so_doc.status or "Draft").strip()
    status_lower = status_map.get(status, status.lower() if status else "draft")

    project_no = ""
    if hasattr(so_doc, "project") and so_doc.project:
        project_no = so_doc.project
    meta = frappe.get_meta("Sales Order")
    if meta.get_field("custom_project_no"):
        project_no = getattr(so_doc, "custom_project_no", None) or project_no

    total_qty = sum(flt(si.get("qty") or si.get("stock_qty") or 0) for si in (so_items or []))
    first_item_name = ""
    first_rate = 0
    first_so_item = so_items[0] if so_items else None
    if so_items:
        first_item_name = (so_items[0].get("item_name") or so_items[0].get("item_code") or "").strip()
        first_rate = flt(so_items[0].get("rate") or 0)

    # 成品（首行）的仓库与库存，供前端在成品行展示
    wh_code, wh_name, inv_qty = _get_finished_product_warehouse_and_stock(so_doc, first_so_item)

    return {
        "orderNo": (so_doc.name or "").strip(),
        "itemName": first_item_name or "多产品",
        "projectNo": (project_no or "").strip(),
        "orderQty": total_qty,
        "unitEstimatedCost": None,
        "salesPrice": first_rate,
        "runningCostRate": 5,
        "transportFeeRate": 3,
        "taxRate": 0.13,
        "grossMargin": None,
        "status": status_lower,
        "warehouseCode": wh_code,
        "warehouseName": wh_name,
        "inventoryQty": inv_qty,
    }


def _build_items(flat_nodes, item_details_cache, finished_product_wh=None):
    """
    将扁平节点列表转为前端所需 items 格式。每 entry 含 so_item，用于 order_qty。

    finished_product_wh: 可选，(warehouse_code, warehouse_name, inventory_qty)，
        用于 items 第一行（BOM 根节点/成品）补充仓库与库存，与 header 保持一致。
    """
    # 收集所有物料组名称，批量查父级
    item_group_names = []
    for entry in flat_nodes:
        node = entry.get("node") or {}
        ic = node.get("item_code")
        details = item_details_cache.get(ic, {})
        ig = (node.get("item_group") or details.get("item_group") or "").strip()
        if ig:
            item_group_names.append(ig)
    item_group_parent_cache = _get_item_group_parent_map(item_group_names)

    items = []
    for row_no, entry in enumerate(flat_nodes, start=1):
        node = entry["node"]
        level = entry["level"]
        bom_code = entry["bom_code"]
        path_ratio = entry["path_ratio"]
        so_item = entry.get("so_item") or {}
        order_qty = flt(so_item.get("qty") or so_item.get("stock_qty") or 0)

        item_code = node.get("item_code") or ""
        item_name = (node.get("item_name") or "").strip() or item_code
        details = item_details_cache.get(item_code, {})

        warehouse = (node.get("warehouse") or "").strip() or details.get("warehouse", "")
        warehouse_name = _get_warehouse_name(warehouse) if warehouse else ""
        supplier = (node.get("supplier") or "").strip() or details.get("supplier", "")
        supplier_name = _get_supplier_name(supplier) if supplier else ""
        process = (node.get("process") or "").strip()

        # estimatedCost: BOM Item.rate 或 Bin.valuation_rate
        estimated_cost = None
        bom_item_id = node.get("id")
        if bom_item_id and frappe.db.exists("BOM Item", bom_item_id):
            estimated_cost = flt(frappe.db.get_value("BOM Item", bom_item_id, "rate"))
        if estimated_cost is None and item_code:
            bin_rows = frappe.get_all(
                "Bin",
                filters={"item_code": item_code},
                fields=["valuation_rate"],
                limit=1,
            )
            if bin_rows and bin_rows[0].get("valuation_rate"):
                estimated_cost = flt(bin_rows[0].valuation_rate)

        loss_ratio = None

        # orderCost = orderQty * path_ratio * estimatedCost * (1 + loss_ratio)
        order_cost = 0
        if estimated_cost is not None:
            loss_factor = 1 + flt(loss_ratio or 0)
            order_cost = round(flt(order_qty) * path_ratio * flt(estimated_cost) * loss_factor, 2)

        inventory_qty = _get_bin_actual_qty(item_code, warehouse) if warehouse else None

        # items 第一行（BOM 根节点/成品）：用 header 同源的成品仓库与库存补全
        if row_no == 1 and finished_product_wh:
            wh_code, wh_name, inv_qty = finished_product_wh
            warehouse = wh_code or warehouse
            warehouse_name = wh_name if wh_code else warehouse_name
            inventory_qty = inv_qty if wh_code else inventory_qty

        ratio_qty = flt(node.get("bom_qty") or 1)
        item_group = (node.get("item_group") or details.get("item_group") or "").strip()
        item_group_parent = item_group_parent_cache.get(item_group, "") if item_group else ""

        row = {
            "id": node.get("id") or "",
            "rowNo": row_no,
            "itemCode": item_code,
            "level": level,
            "bomCode": bom_code,
            "itemName": item_name,
            "item_group": item_group,
            "item_group_parent": item_group_parent,
            "ratioQty": ratio_qty,
            "inventoryQty": inventory_qty,
            "estimatedCost": estimated_cost,
            "lossRatio": loss_ratio,
            "orderCost": order_cost,
            "warehouseCode": warehouse or "",
            "warehouseName": warehouse_name,
            "supplierCode": supplier or "",
            "supplierName": supplier_name,
            "process": process,
            "orderStatus": "未生单",
            "purchaseOrderNo": None,
            "receivedQty": None,
            "unreceivedQty": None,
            "orderConfirmationStatus": "",
            "warehouseSlot": None,
            "itemGroup": item_group,
        }
        items.append(row)
    return items


def _get_leaf_finished_products(flat_nodes, item_details_cache):
    """
    从扁平节点中找出末级成品（item_group 成品，且其子节点中无成品）。
    返回: [(entry, item_group), ...]，entry 含 node, level, bom_code, path_ratio, so_item
    """
    # 构建 bom_code -> item_group 映射
    code_to_ig = {}
    for e in flat_nodes:
        node = e.get("node") or {}
        ic = node.get("item_code")
        ig = (node.get("item_group") or (item_details_cache.get(ic) or {}).get("item_group") or "").strip()
        code_to_ig[e.get("bom_code", "")] = ig

    result = []
    for e in flat_nodes:
        bc = (e.get("bom_code") or "").strip()
        ig = code_to_ig.get(bc, "")
        if ig != "成品":
            continue
        # 检查是否存在子节点且子节点是成品
        has_finished_child = False
        for other in flat_nodes:
            obc = (other.get("bom_code") or "").strip()
            if obc.startswith(bc + "-") and code_to_ig.get(obc, "") == "成品":
                has_finished_child = True
                break
        if not has_finished_child:
            result.append((e, ig))
    return result


def _fetch_item_carton_and_packaging(item_code):
    """
    从 Item 获取纸箱、包材相关数据。
    返回: (br_carton_spec, br_packing_qty, br_volume, br_supplier, br_price, br_packaging_details)
    """
    meta = frappe.get_meta("Item")
    fields = ["name"]
    for f in ("br_carton_spec", "br_packing_qty", "br_volume", "br_supplier", "br_price"):
        if meta.get_field(f):
            fields.append(f)
    row = frappe.db.get_value("Item", item_code, fields, as_dict=True) or {}
    carton_spec = (row.get("br_carton_spec") or "").strip()
    packing_qty = flt(row.get("br_packing_qty") or 0)
    volume = (row.get("br_volume") or "").strip()
    supplier = (row.get("br_supplier") or "").strip()
    price = flt(row.get("br_price") or 0)

    details = []
    if frappe.db.exists("Item", item_code):
        doc = frappe.get_cached_doc("Item", item_code)
        details = list(getattr(doc, "br_packaging_details", []) or [])

    return carton_spec, packing_qty, volume, supplier, price, details


def _get_finished_product_from_component(component_item_code):
    """
    SO 行是组件时：查找以该组件为子件的 BOM，返回 BOM 根物料的 成品（即父级成品）。
    若组件属于某 成品的 BOM，返回该 成品 item_code；否则返回 None。
    """
    if not (component_item_code or "").strip():
        return None
    bom_names = frappe.get_all(
        "BOM Item",
        filters={"item_code": component_item_code},
        fields=["parent"],
        limit_page_length=10,
    )
    for r in bom_names:
        bom_name = r.get("parent")
        if not bom_name or not frappe.db.exists("BOM", bom_name):
            continue
        root_item = frappe.db.get_value("BOM", bom_name, "item")
        if not root_item:
            continue
        ig = (frappe.db.get_value("Item", root_item, "item_group") or "").strip()
        if ig == "成品":
            return root_item
    return None


def _build_carton_and_packaging_from_leaf_finished(flat_nodes, item_details_cache, so_items):
    """
    根据末级成品从 Item 主数据取 br_carton_spec、br_packaging_details，构建 cartonItems、packagingItems。
    同时处理：SO 行为组件时，其所属的 成品（BOM 根）若有纸箱/包材，也一并加入。
    """
    leaf_finished = _get_leaf_finished_products(flat_nodes, item_details_cache)
    carton_items = []
    packaging_items = []
    added_finished = set()  # 已处理的 成品，避免重复
    ig_parent_cache = {}  # item_group -> item_group_parent，按需填充

    for entry, _ in leaf_finished:
        node = entry.get("node") or {}
        bom_code = (entry.get("bom_code") or "").strip()
        path_ratio = flt(entry.get("path_ratio") or 1)
        so_item = entry.get("so_item") or {}
        order_qty = flt(so_item.get("qty") or so_item.get("stock_qty") or 0)
        item_code = node.get("item_code") or ""

        added_finished.add(item_code)
        carton_spec, packing_qty, volume, supplier, price, pack_details = _fetch_item_carton_and_packaging(item_code)

        # 纸箱：br_carton_spec 链接到包材 Item（纸箱）
        if carton_spec and frappe.db.exists("Item", carton_spec):
            carton_qty = round(order_qty * path_ratio / (packing_qty or 1), 4) if packing_qty else order_qty * path_ratio
            # ratioQty：直接传成品 Item 的装箱数，前端自行换算
            carton_name = frappe.db.get_value("Item", carton_spec, "item_name") or carton_spec
            carton_ig = (frappe.db.get_value("Item", carton_spec, "item_group") or "").strip() or "纸箱"
            if carton_ig not in ig_parent_cache:
                ig_parent_cache.update(_get_item_group_parent_map([carton_ig]))
            carton_ig_parent = ig_parent_cache.get(carton_ig, "")
            carton_items.append({
                "id": "",
                "rowNo": len(carton_items) + 1,
                "itemCode": carton_spec,
                "level": 1,
                "bomCode": bom_code + "-C",
                "itemName": carton_name,
                "ratioQty": flt(packing_qty) if packing_qty else 1.0,
                "inventoryQty": None,
                "estimatedCost": price or None,
                "lossRatio": None,
                "orderCost": round(order_qty * path_ratio * (price / (packing_qty or 1)), 2) if packing_qty and price else 0,
                "warehouseCode": "",
                "warehouseName": "",
                "supplierCode": supplier,
                "supplierName": _get_supplier_name(supplier),
                "process": "",
                "orderStatus": "未生单",
                "purchaseOrderNo": None,
                "receivedQty": None,
                "unreceivedQty": None,
                "orderConfirmationStatus": "",
                "warehouseSlot": None,
                "itemGroup": carton_ig,
                "item_group": carton_ig,
                "item_group_parent": carton_ig_parent,
            })

        # 包材：br_packaging_details 子表
        RATIO_BASE = 5000.0
        pkg_ig = "包材"
        if pkg_ig not in ig_parent_cache:
            ig_parent_cache.update(_get_item_group_parent_map([pkg_ig]))
        pkg_ig_parent = ig_parent_cache.get(pkg_ig, "")
        for idx, pd in enumerate(pack_details):
            pitem = (getattr(pd, "br_packaging_item", None) or "").strip()
            pmodel = (getattr(pd, "br_packaging_model", None) or "").strip()
            pratio = flt(getattr(pd, "br_packaging_ratio", None) or 0)
            psupp = (getattr(pd, "br_supplier_one", None) or "").strip()
            pprice = flt(getattr(pd, "br_price_one", None) or 0)
            pname = pitem + (" " + pmodel if pmodel else "")
            need_qty = (order_qty * path_ratio / RATIO_BASE) * pratio if pratio else 0
            packaging_items.append({
                "id": "",
                "rowNo": len(packaging_items) + 1,
                "itemCode": pitem or pname,
                "level": 1,
                "bomCode": bom_code + "-P" + str(idx + 1),
                "itemName": pname or pitem,
                "ratioQty": pratio,
                "inventoryQty": None,
                "estimatedCost": pprice or None,
                "lossRatio": None,
                "orderCost": round(need_qty * pprice, 2) if pprice else 0,
                "warehouseCode": "",
                "warehouseName": "",
                "supplierCode": psupp,
                "supplierName": _get_supplier_name(psupp),
                "process": "",
                "orderStatus": "未生单",
                "purchaseOrderNo": None,
                "receivedQty": None,
                "unreceivedQty": None,
                "orderConfirmationStatus": "",
                "warehouseSlot": None,
                "itemGroup": pkg_ig,
                "item_group": pkg_ig,
                "item_group_parent": pkg_ig_parent,
            })

    # 补充：SO 行为组件时，其所属 成品 的纸箱/包材
    for row_idx, so_item in enumerate(so_items or []):
        comp_code = (so_item.get("item_code") or "").strip()
        if not comp_code:
            continue
        finished_code = _get_finished_product_from_component(comp_code)
        if not finished_code or finished_code in added_finished:
            continue
        added_finished.add(finished_code)
        order_qty = flt(so_item.get("qty") or so_item.get("stock_qty") or 0)
        bom_code = "A" + str(row_idx + 1)
        carton_spec, packing_qty, volume, supplier, price, pack_details = _fetch_item_carton_and_packaging(finished_code)

        if carton_spec and frappe.db.exists("Item", carton_spec):
            carton_name = frappe.db.get_value("Item", carton_spec, "item_name") or carton_spec
            carton_ig = (frappe.db.get_value("Item", carton_spec, "item_group") or "").strip() or "纸箱"
            if carton_ig not in ig_parent_cache:
                ig_parent_cache.update(_get_item_group_parent_map([carton_ig]))
            carton_ig_parent = ig_parent_cache.get(carton_ig, "")
            carton_items.append({
                "id": "", "rowNo": len(carton_items) + 1, "itemCode": carton_spec, "level": 1,
                "bomCode": bom_code + "-C", "itemName": carton_name, "ratioQty": flt(packing_qty) if packing_qty else 1.0,
                "inventoryQty": None, "estimatedCost": price or None, "lossRatio": None,
                "orderCost": round(order_qty * (price / (packing_qty or 1)), 2) if packing_qty and price else 0,
                "warehouseCode": "", "warehouseName": "", "supplierCode": supplier,
                "supplierName": _get_supplier_name(supplier), "process": "", "orderStatus": "未生单",
                "purchaseOrderNo": None, "receivedQty": None, "unreceivedQty": None,
                "orderConfirmationStatus": "", "warehouseSlot": None,
                "itemGroup": carton_ig, "item_group": carton_ig, "item_group_parent": carton_ig_parent,
            })

        RATIO_BASE = 5000.0
        for idx, pd in enumerate(pack_details):
            pitem = (getattr(pd, "br_packaging_item", None) or "").strip()
            pmodel = (getattr(pd, "br_packaging_model", None) or "").strip()
            pratio = flt(getattr(pd, "br_packaging_ratio", None) or 0)
            psupp = (getattr(pd, "br_supplier_one", None) or "").strip()
            pprice = flt(getattr(pd, "br_price_one", None) or 0)
            pname = pitem + (" " + pmodel if pmodel else "")
            need_qty = (order_qty / RATIO_BASE) * pratio if pratio else 0
            packaging_items.append({
                "id": "", "rowNo": len(packaging_items) + 1, "itemCode": pitem or pname, "level": 1,
                "bomCode": bom_code + "-P" + str(idx + 1), "itemName": pname or pitem, "ratioQty": pratio,
                "inventoryQty": None, "estimatedCost": pprice or None, "lossRatio": None,
                "orderCost": round(need_qty * pprice, 2) if pprice else 0,
                "warehouseCode": "", "warehouseName": "", "supplierCode": psupp,
                "supplierName": _get_supplier_name(psupp), "process": "", "orderStatus": "未生单",
                "purchaseOrderNo": None, "receivedQty": None, "unreceivedQty": None,
                "orderConfirmationStatus": "", "warehouseSlot": None,
                "itemGroup": pkg_ig, "item_group": pkg_ig, "item_group_parent": pkg_ig_parent,
            })

    return carton_items, packaging_items


def _collect_item_codes_from_flat(flat_nodes):
    """从扁平节点收集所有 item_code，用于批量获取 item_details"""
    codes = []
    for e in flat_nodes:
        ic = (e.get("node") or {}).get("item_code")
        if ic:
            codes.append(ic)
    return list(set(codes))


def _parse_list_bom_material_report_kwargs(kwargs):
    """支持直接 kwargs 或 json_data（字符串 / dict），与其它销售白名单一致。"""
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


def _escape_like_pattern(text):
    """避免 LIKE 通配符注入。"""
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _display_bom_status(raw):
    """主表 status 与前端展示文案对齐（未审核 / 已审核）。"""
    s = (raw or "").strip().lower()
    if s in ("draft", "未审核"):
        return "未审核"
    if s in ("approved", "submitted", "已审核"):
        return "已审核"
    if not (raw or "").strip():
        return "未审核"
    return (raw or "").strip()


def _normalize_filter_bom_status(bom_status):
    """筛选条件可与库内英文或中文状态兼容。"""
    s = (bom_status or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in ("未审核",) or low == "draft":
        return ["status", "in", ["draft", "未审核"]]
    if low in ("已审核",) or low in ("approved", "submitted"):
        return ["status", "in", ["approved", "submitted", "已审核"]]
    return ["status", "=", s]


def _iso_date_ymd(val):
    """API 统一输出 YYYY-MM-DD，与前端报表约定一致（不受用户日期格式影响）。"""
    if not val:
        return ""
    try:
        return getdate(val).strftime("%Y-%m-%d")
    except Exception:
        return (str(val) or "")[:10]


def _row_to_bom_report_item(row, idx):
    """BR SO BOM List 一行 -> 前端 BomReportRow 结构。"""
    order_no = (row.get("order_no") or "").strip()
    item_code = (row.get("item_code") or "").strip()
    name = (row.get("name") or "").strip()
    doc_id = name or "{}::{}::{}".format(order_no, item_code, idx)
    delivery = row.get("delivery_date")
    delivery_str = _iso_date_ymd(delivery) if delivery else ""
    approved_on = row.get("approved_on")
    audit_date_str = _iso_date_ymd(approved_on) if approved_on else ""

    return {
        "id": doc_id,
        "bomStatus": _display_bom_status(row.get("status")),
        "salesOrderNo": order_no,
        "unitCode": (row.get("customer_code") or "").strip(),
        "unitName": (row.get("customer_name") or "").strip(),
        "itemCode": item_code,
        "itemName": (row.get("item_name") or "").strip(),
        "deliveryDate": delivery_str,
        "materialAuditor": (row.get("approved_by") or "").strip(),
        "materialAuditDate": audit_date_str,
        "documentCreator": (row.get("created_by") or "").strip(),
    }


@frappe.whitelist(allow_guest=False)
def list_bom_material_report(**kwargs):
    """
    BOM 物料清单报表 — 列表页数据。

    数据源：**BR SO BOM List**（一行 = 销售订单号 + 成品物料），与同步写入的主表一致。
    不展开子表 BR SO BOM List Details；明细页仍用 get_product_bom_list。

    日期口径：**delivery_date（交货日期）** 闭区间 [date_from, date_to]。
    无交货日期的主表记录不会命中日期筛选。

    请求参数（可直接作为表单字段，或放在 json_data 内）:
        date_from (str): 必填，YYYY-MM-DD
        date_to (str): 必填，YYYY-MM-DD
        page_number (int): 可选，默认 1
        page_size (int): 可选，默认 20，最大 100
        sales_order_name (str): 可选，精确匹配主表 order_no
        customer (str): 可选，精确匹配 customer_code（单位编号）
        customer_name (str): 可选，customer_name 模糊匹配
        bom_status (str): 可选，与 bomStatus 展示一致时可传「未审核」「已审核」，或与库内 status 一致
        item_code (str): 可选，存货编码模糊匹配

    返回:
        success=True: { "data": { "page_number", "page_size", "total_count", "total_pages", "items": [...] } }
        success=False: { "message": "..." }

    权限: 遵循 Frappe 对 DocType **BR SO BOM List** 的读权限（需能 read 该 DocType）。
    """
    jd = _parse_list_bom_material_report_kwargs(kwargs)
    date_from = (jd.get("date_from") or "").strip()
    date_to = (jd.get("date_to") or "").strip()

    if not date_from or not date_to:
        return {"success": False, "message": "date_from 与 date_to 不能为空（格式 YYYY-MM-DD）"}

    try:
        df = getdate(date_from)
        dt = getdate(date_to)
    except Exception:
        return {"success": False, "message": "日期格式非法，请使用 YYYY-MM-DD"}

    if df > dt:
        return {"success": False, "message": "date_from 不能晚于 date_to"}

    page_number = cint(jd.get("page_number") or 1, 1)
    page_size = cint(jd.get("page_size") or 20, 20)
    if page_number < 1:
        page_number = 1
    if page_size < 1:
        page_size = 20
    if page_size > 100:
        page_size = 100

    sales_order_name = (jd.get("sales_order_name") or "").strip()
    customer = (jd.get("customer") or "").strip()
    customer_name = (jd.get("customer_name") or "").strip()
    bom_status = (jd.get("bom_status") or "").strip()
    item_code = (jd.get("item_code") or "").strip()

    filters = [
        ["delivery_date", ">=", df],
        ["delivery_date", "<=", dt],
    ]

    if sales_order_name:
        filters.append(["order_no", "=", sales_order_name])
    if customer:
        filters.append(["customer_code", "=", customer])
    if customer_name:
        esc = _escape_like_pattern(customer_name)
        filters.append(["customer_name", "like", "%" + esc + "%"])
    if bom_status:
        st_f = _normalize_filter_bom_status(bom_status)
        if st_f:
            filters.append(st_f)
    if item_code:
        esc = _escape_like_pattern(item_code)
        filters.append(["item_code", "like", "%" + esc + "%"])

    fields = [
        "name",
        "order_no",
        "status",
        "customer_code",
        "customer_name",
        "item_code",
        "item_name",
        "delivery_date",
        "approved_by",
        "approved_on",
        "created_by",
    ]

    order_by = "delivery_date desc, order_no desc, item_code asc"

    try:
        frappe.has_permission("BR SO BOM List", "read", throw=True)
    except frappe.PermissionError:
        return {"success": False, "message": "无权限访问 BOM 物料清单报表"}

    try:
        total_list = frappe.get_list(
            "BR SO BOM List",
            filters=filters,
            fields=["name"],
            limit_page_length=0,
            ignore_permissions=False,
        )
        total_count = len(total_list)

        limit_start = (page_number - 1) * page_size
        rows = frappe.get_list(
            "BR SO BOM List",
            filters=filters,
            fields=fields,
            order_by=order_by,
            limit_start=limit_start,
            limit_page_length=page_size,
            ignore_permissions=False,
        )

        items = []
        for i, row in enumerate(rows):
            items.append(_row_to_bom_report_item(row, limit_start + i))

        total_pages = int(math.ceil(float(total_count) / page_size)) if page_size else 0

        return {
            "success": True,
            "message": None,
            "data": {
                "page_number": page_number,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages,
                "items": items,
            },
        }
    except Exception as e:
        frappe.log_error(
            title="list_bom_material_report",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_product_bom_list(sales_order_name=None, item_code=None):
    """
    获取产品物料清单详情。以 Sales Order Detail 每一行为入口，展开完整 BOM 层级。

    - 每一行 SO Detail 都展开：有 BOM 则展开所有层级（含该行物料本身），无 BOM 则仅输出该物料
    - items 含 level、bomCode，体现完整层次关系

    入参:
        sales_order_name (str): 销售订单 name，必填
        item_code (str): 可选；若传则仅展开该行，不传则展开所有行

    返回:
        success=True: { "data": { "header": {...}, "items": [...] } }
        success=False: { "message": "..." }
    """
    sales_order_name = (sales_order_name or "").strip()
    item_code = (item_code or "").strip()

    if not sales_order_name:
        return {"success": False, "message": "sales_order_name 不能为空"}

    try:
        if not frappe.db.exists("Sales Order", sales_order_name):
            return {"success": False, "message": "销售订单不存在或无权访问"}

        so_doc = frappe.get_doc("Sales Order", sales_order_name)
        frappe.has_permission("Sales Order", doc=so_doc, throw=True)

        so_items = list(so_doc.items or [])
        if not so_items:
            header = _build_header(so_doc, [])
            return {
                "success": True,
                "data": {
                    "header": header,
                    "items": [],
                    "cartonItems": [],
                    "packagingItems": [],
                },
            }

        # 若传入 item_code，仅处理该成品行（用于按「销售订单+成品」维度写入 BOM 两表）
        if item_code:
            so_items = [si for si in so_items if (si.item_code or "").strip() == item_code]
            if not so_items:
                return {
                    "success": False,
                    "message": "销售订单中未找到指定成品行: {}".format(item_code),
                }

        # 处理 SO Detail 行（全部或仅 item_code 指定的一行）

        flat = []
        for row_idx, so_item in enumerate(so_items):
            target_item_code = (so_item.item_code or "").strip()
            if not target_item_code:
                continue

            root_bom_code = "A" + str(row_idx + 1)

            bom_name = _get_bom_for_item(target_item_code, getattr(so_item, "bom_no", None))
            if bom_name:
                tree = _build_bom_tree(bom_name)
                if tree:
                    # 含根节点：配件 -> 组件 -> 半成品
                    _flatten_bom_tree_with_root(
                        tree, level=1, parent_bom_code=root_bom_code, flat=flat,
                        path_ratio=1.0, so_item=so_item, include_root=True,
                    )
                    continue

            # 无 BOM：仅输出该物料本身
            root_node = _so_item_to_root_node(so_item)
            flat.append({
                "node": root_node,
                "level": 1,
                "bom_code": root_bom_code,
                "path_ratio": 1.0,
                "so_item": so_item,
            })

        item_codes = _collect_item_codes_from_flat(flat)
        item_details_cache = _get_item_tree_fields(item_codes) if item_codes else {}
        # 成品（首行）的仓库与库存，补全 items 第一行，与 header 保持一致
        first_so_item = so_items[0] if so_items else None
        finished_product_wh = _get_finished_product_warehouse_and_stock(so_doc, first_so_item)
        items = _build_items(flat, item_details_cache, finished_product_wh=finished_product_wh)
        carton_items, packaging_items = _build_carton_and_packaging_from_leaf_finished(flat, item_details_cache, so_items)

        total_cost = sum(flt(r.get("orderCost") or 0) for r in items)
        total_qty = sum(flt(si.get("qty") or si.get("stock_qty") or 0) for si in so_items)
        unit_estimated_cost = round(total_cost / total_qty, 4) if total_qty else None

        header = _build_header(so_doc, so_items)
        header["unitEstimatedCost"] = unit_estimated_cost

        sales_price = flt(header.get("salesPrice") or 0)
        if sales_price and unit_estimated_cost is not None:
            header["grossMargin"] = round((sales_price - unit_estimated_cost) / sales_price, 4)

        return {
            "success": True,
            "data": {
                "header": header,
                "items": items,
                "cartonItems": carton_items,
                "packagingItems": packaging_items,
            },
        }

    except frappe.PermissionError:
        return {"success": False, "message": "销售订单不存在或无权访问"}
    except Exception as e:
        frappe.log_error(
            title="get_product_bom_list",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}
