# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
产品物料清单 API：基于销售订单明细行，展开完整 BOM 层级结构。

- get_product_bom_list: 获取单条产品物料清单详情（header + 扁平 items，含 level、bomCode）；数据源为 BOM + Item 实时展开
- get_product_bom_list_new: 与上者返回结构相同，数据源为已同步的 BR SO BOM List + BR SO BOM List Details
- list_bom_material_report: BOM 物料清单报表列表（数据源：BR SO BOM List 主表，一行 = 一订单 + 一成品）

bench execute 示例:
    bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list --kwargs '{"sales_order_name": "SAL-ORD-2026-00003", "item_code": "配件_mm4io3o2ua3k"}'
    bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list_new --kwargs '{"sales_order_name": "SAL-ORD-2026-00003", "item_code": "配件_mm4io3o2ua3k"}'
    bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report --kwargs '{"json_data": {"date_from": "2025-01-01", "date_to": "2026-12-31", "page_number": 1, "page_size": 20}}'
"""

from __future__ import unicode_literals

import json
import math
import re

import frappe
from frappe.utils import cint, flt, getdate

from bairun_erp.utils.api.material.bom_query import (
    _attach_process_supplier_rows,
    _build_bom_tree,
    _get_item_tree_fields,
    get_item_process_supplier_row_for_resolved_process,
)


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


def _get_item_warehouse_stock_for_company(item_code, company):
    """
    按 Item Default（公司维度）取物料默认仓，并补仓库名称与 Bin 库存。
    用于纸箱/包材等未走 BOM 展开的行，与 _build_items 中仓库口径一致。
    """
    item_code = (item_code or "").strip()
    company = (company or "").strip()
    if not item_code or not company or not frappe.db.exists("Item", item_code):
        return "", "", None
    wh = _get_item_default_warehouse(item_code, company)
    if not wh:
        return "", "", None
    wh_name = _get_warehouse_name(wh) or ""
    inv_qty = _get_bin_actual_qty(item_code, wh)
    return wh, wh_name, inv_qty


def _resolve_packaging_row_item_code_for_warehouse(br_packaging_item, br_packaging_model):
    """
    包材子表 br_packaging_item 为 Select（吸塑/PE膜等），一般不是 Item 编码；
    实际物料编码常在 br_packaging_model（如 STO-ITEM-2026-00009、BLISTER-xxx）。
    返回第一个在 Item 中存在的候选，用于 Item Default 取仓。
    """
    pitem = (br_packaging_item or "").strip()
    pmodel = (br_packaging_model or "").strip()
    for candidate in (pmodel, pitem):
        if candidate and frappe.db.exists("Item", candidate):
            return candidate
    return ""


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

        # 结构行：BOM rate / 库存估价仍为空或 0 时，用 Item 子表「工艺-供应商」与 process 对齐的一行补供应商一、价格一
        matched_ps = get_item_process_supplier_row_for_resolved_process(details, process)
        if matched_ps:
            psupp = (matched_ps.get("br_supplier_one") or "").strip()
            if not (supplier or "").strip() and psupp:
                supplier = psupp
                supplier_name = _get_supplier_name(supplier)
            if estimated_cost is None or flt(estimated_cost) == 0:
                pv = matched_ps.get("br_price_one")
                if pv is not None and str(pv).strip() != "":
                    estimated_cost = flt(pv)

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


def _build_carton_and_packaging_from_leaf_finished(flat_nodes, item_details_cache, so_items, company=None):
    """
    根据末级成品从 Item 主数据取 br_carton_spec、br_packaging_details，构建 cartonItems、packagingItems。
    同时处理：SO 行为组件时，其所属的 成品（BOM 根）若有纸箱/包材，也一并加入。
    company：销售订单公司，用于 Item Default 默认仓（纸箱/包材物料）。
    """
    company = (company or "").strip()
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
            c_wh, c_wh_name, c_inv = _get_item_warehouse_stock_for_company(carton_spec, company)
            carton_items.append({
                "id": "",
                "rowNo": len(carton_items) + 1,
                "itemCode": carton_spec,
                "level": 1,
                "bomCode": bom_code + "-C",
                "itemName": carton_name,
                "ratioQty": flt(packing_qty) if packing_qty else 1.0,
                "inventoryQty": c_inv,
                "estimatedCost": price or None,
                "lossRatio": None,
                "orderCost": round(order_qty * path_ratio * (price / (packing_qty or 1)), 2) if packing_qty and price else 0,
                "warehouseCode": c_wh,
                "warehouseName": c_wh_name,
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
            pack_ic = _resolve_packaging_row_item_code_for_warehouse(pitem, pmodel)
            p_wh, p_wh_name, p_inv = _get_item_warehouse_stock_for_company(pack_ic, company)
            packaging_items.append({
                "id": "",
                "rowNo": len(packaging_items) + 1,
                "itemCode": pitem or pname,
                "level": 1,
                "bomCode": bom_code + "-P" + str(idx + 1),
                "itemName": pname or pitem,
                "ratioQty": pratio,
                "inventoryQty": p_inv,
                "estimatedCost": pprice or None,
                "lossRatio": None,
                "orderCost": round(need_qty * pprice, 2) if pprice else 0,
                "warehouseCode": p_wh,
                "warehouseName": p_wh_name,
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
            c2_wh, c2_wh_name, c2_inv = _get_item_warehouse_stock_for_company(carton_spec, company)
            carton_items.append({
                "id": "", "rowNo": len(carton_items) + 1, "itemCode": carton_spec, "level": 1,
                "bomCode": bom_code + "-C", "itemName": carton_name, "ratioQty": flt(packing_qty) if packing_qty else 1.0,
                "inventoryQty": c2_inv, "estimatedCost": price or None, "lossRatio": None,
                "orderCost": round(order_qty * (price / (packing_qty or 1)), 2) if packing_qty and price else 0,
                "warehouseCode": c2_wh, "warehouseName": c2_wh_name, "supplierCode": supplier,
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
            pack_ic2 = _resolve_packaging_row_item_code_for_warehouse(pitem, pmodel)
            p2_wh, p2_wh_name, p2_inv = _get_item_warehouse_stock_for_company(pack_ic2, company)
            packaging_items.append({
                "id": "", "rowNo": len(packaging_items) + 1, "itemCode": pitem or pname, "level": 1,
                "bomCode": bom_code + "-P" + str(idx + 1), "itemName": pname or pitem, "ratioQty": pratio,
                "inventoryQty": p2_inv, "estimatedCost": pprice or None, "lossRatio": None,
                "orderCost": round(need_qty * pprice, 2) if pprice else 0,
                "warehouseCode": p2_wh, "warehouseName": p2_wh_name, "supplierCode": psupp,
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


# list_bom_material_report：order_by 仅允许主表真实字段，防注入
_BOM_REPORT_ORDER_COLS = frozenset(
    {
        "creation",
        "modified",
        "delivery_date",
        "order_no",
        "item_code",
        "name",
        "status",
        "customer_code",
        "customer_name",
    }
)


def _sanitize_bom_report_order_by(order_by_raw):
    """
    默认 creation 降序；可选单字段 + asc/desc。
    主排序非 name 时追加 name desc 作稳定次序，避免分页抖动。
    """
    default_col, default_dir = "creation", "desc"

    def _full(primary_col, primary_dir):
        if primary_col == "name":
            return "{0} {1}".format(primary_col, primary_dir)
        return "{0} {1}, name desc".format(primary_col, primary_dir)

    ob = (order_by_raw or "").strip()
    if not ob:
        return _full(default_col, default_dir)
    parts = ob.split()
    col = (parts[0] or "").lower()
    if col not in _BOM_REPORT_ORDER_COLS:
        return _full(default_col, default_dir)
    if len(parts) == 1:
        direc = "desc"
    else:
        direc = "desc" if parts[1].lower() == "desc" else "asc"
    return _full(col, direc)


def _format_bom_report_creation(val):
    """主表 creation 回传字符串，与前端约定一致（含微秒）。"""
    if val is None or val == "":
        return ""
    if isinstance(val, str):
        return val.strip()
    try:
        return val.strftime("%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        return (str(val) or "").strip()


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
        "creation": _format_bom_report_creation(row.get("creation")),
    }


@frappe.whitelist(allow_guest=False)
def list_bom_material_report(**kwargs):
    """
    BOM 物料清单报表 — 列表页数据。

    数据源：**BR SO BOM List**（一行 = 销售订单号 + 成品物料），与同步写入的主表一致。
    不展开子表 BR SO BOM List Details；明细页可用 get_product_bom_list（实时 BOM）或 get_product_bom_list_new（已同步主从表）。

    日期口径：**creation（创建时间）** 按日期闭区间 [date_from, date_to]。
    即：creation >= date_from 00:00:00 且 creation <= date_to 23:59:59。

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
        order_by (str): 可选，默认 creation desc；仅允许主表字段（creation、modified、delivery_date 等），见实现内白名单

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
    order_by = _sanitize_bom_report_order_by(jd.get("order_by"))

    date_from_dt = "{} 00:00:00".format(df.strftime("%Y-%m-%d"))
    date_to_dt = "{} 23:59:59".format(dt.strftime("%Y-%m-%d"))

    filters = [
        ["creation", ">=", date_from_dt],
        ["creation", "<=", date_to_dt],
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
        "creation",
    ]

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
        if item_details_cache:
            _attach_process_supplier_rows(item_details_cache, list(item_details_cache.keys()))
        # 成品（首行）的仓库与库存，补全 items 第一行，与 header 保持一致
        first_so_item = so_items[0] if so_items else None
        finished_product_wh = _get_finished_product_warehouse_and_stock(so_doc, first_so_item)
        items = _build_items(flat, item_details_cache, finished_product_wh=finished_product_wh)
        carton_items, packaging_items = _build_carton_and_packaging_from_leaf_finished(
            flat, item_details_cache, so_items, company=getattr(so_doc, "company", None) or ""
        )

        total_cost = sum(flt(r.get("orderCost") or 0) for r in items)
        total_qty = sum(flt(si.get("qty") or si.get("stock_qty") or 0) for si in so_items)
        unit_estimated_cost = round(total_cost / total_qty, 4) if total_qty else None

        finished_codes = [(getattr(si, "item_code", None) or "").strip() for si in so_items]

        header = _build_header(so_doc, so_items)
        header["status"] = _resolve_br_so_bom_header_status(
            sales_order_name,
            finished_codes,
            header.get("status"),
        )
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


_PKG_BOM_CODE_SUFFIX = re.compile(r"-P\d+$")


def _br_so_bom_list_doc_name(order_no, finished_item_code):
    return "{}-{}".format((order_no or "").strip(), (finished_item_code or "").strip())


def _sort_bom_list_detail_rows(details):
    rows = list(details or [])

    def _key(r):
        rn = getattr(r, "row_no", None)
        if rn is not None:
            return (0, cint(rn, 0))
        return (1, cint(getattr(r, "idx", None), 0))

    rows.sort(key=_key)
    return rows


def _split_stored_bom_detail_rows(detail_rows):
    """与 sales_order_bom_sync 写入顺序一致：items + carton（bom_code 后缀 -C）+ packaging（-P 数字）。"""
    items, cartons, packs = [], [], []
    for row in detail_rows:
        bc = (getattr(row, "bom_code", None) or "").strip()
        if bc.endswith("-C"):
            cartons.append(row)
        elif _PKG_BOM_CODE_SUFFIX.search(bc):
            packs.append(row)
        else:
            items.append(row)
    return items, cartons, packs


def _detail_row_to_product_bom_api_row(row, row_no, ig_parent_cache):
    """BR SO BOM List Details 一行 -> get_product_bom_list 中单行结构（camelCase）。"""
    item_group = (getattr(row, "item_group", None) or "").strip()
    bom_code = (getattr(row, "bom_code", None) or "").strip()
    item_code = (getattr(row, "item_code", None) or "").strip()
    item_name = (getattr(row, "item_name", None) or "").strip() or item_code
    wh = (getattr(row, "warehouse_code", None) or "").strip()
    wh_name = (getattr(row, "warehouse_name", None) or "").strip()
    supp = (getattr(row, "supplier_code", None) or "").strip()
    supp_name = (getattr(row, "supplier_name", None) or "").strip()
    if wh and not wh_name:
        wh_name = _get_warehouse_name(wh) or wh_name
    if supp and not supp_name:
        supp_name = _get_supplier_name(supp) or supp_name

    po_raw = (getattr(row, "purchase_order_no", None) or "").strip()
    po = po_raw or None
    po_list = [x.strip() for x in po_raw.split(",") if x and x.strip()]
    # 后端统一口径：有任意采购单号即视为已生单；无单号为未生单。
    normalized_order_status = (getattr(row, "order_status", None) or "").strip()
    if not normalized_order_status:
        normalized_order_status = "已生单" if po_list else "未生单"
    oconf = (getattr(row, "order_confirmation_status", None) or "").strip() or ""
    slot = (getattr(row, "warehouse_slot", None) or "").strip() or None

    loss_raw = getattr(row, "loss_ratio", None)
    inv_raw = getattr(row, "inventory_qty", None)
    est_raw = getattr(row, "estimated_cost", None)
    rec_raw = getattr(row, "received_qty", None)
    unr_raw = getattr(row, "unreceived_qty", None)

    return {
        "id": getattr(row, "name", None) or "",
        "rowNo": row_no,
        "itemCode": item_code,
        "level": cint(getattr(row, "level", None), 0) or 1,
        "bomCode": bom_code,
        "itemName": item_name,
        "item_group": item_group,
        "item_group_parent": ig_parent_cache.get(item_group, "") if item_group else "",
        "ratioQty": flt(getattr(row, "ratio_qty", None)),
        "inventoryQty": flt(inv_raw) if inv_raw is not None else None,
        "estimatedCost": flt(est_raw) if est_raw is not None else None,
        "lossRatio": flt(loss_raw) if loss_raw is not None else None,
        "orderCost": flt(getattr(row, "order_cost", None) or 0),
        "warehouseCode": wh,
        "warehouseName": wh_name,
        "supplierCode": supp,
        "supplierName": supp_name,
        "process": (getattr(row, "process_name", None) or "").strip(),
        "orderStatus": normalized_order_status,
        "order_status": normalized_order_status,
        "purchaseOrderNo": po,
        "purchase_order_no": po,
        "purchase_order_nos": po_list,
        "receivedQty": flt(rec_raw) if rec_raw is not None else None,
        "unreceivedQty": flt(unr_raw) if unr_raw is not None else None,
        "orderConfirmationStatus": oconf,
        "warehouseSlot": slot,
        "itemGroup": item_group,
    }




def _resolve_br_so_bom_header_status(order_no, finished_item_codes, fallback_status):
    """优先使用 BR SO BOM List 主表 status（单成品时最准确）；多成品若状态不一致则回退原值。"""
    order_no = (order_no or "").strip()
    statuses = []
    for ic in finished_item_codes or []:
        ic = (ic or "").strip()
        if not ic:
            continue
        name = _br_so_bom_list_doc_name(order_no, ic)
        st = frappe.db.get_value("BR SO BOM List", name, "status")
        st = (st or "").strip()
        if st:
            statuses.append(st)
    if not statuses:
        return fallback_status
    if len(set(statuses)) == 1:
        return statuses[0]
    return fallback_status

def _load_flat_product_bom_rows_from_br_so_bom_list(order_no, finished_item_codes):
    """
    按销售订单行顺序依次读取 BR SO BOM List，拼接子表行（已按 row_no 排序）。
    返回: (all_item_rows, all_carton_rows, all_pack_rows, missing_item_codes)
    """
    all_item_rows = []
    all_carton_rows = []
    all_pack_rows = []
    missing = []
    order_no = (order_no or "").strip()
    for ic in finished_item_codes:
        ic = (ic or "").strip()
        if not ic:
            continue
        name = _br_so_bom_list_doc_name(order_no, ic)
        if not frappe.db.exists("BR SO BOM List", name):
            missing.append(ic)
            continue
        doc = frappe.get_doc("BR SO BOM List", name)
        frappe.has_permission("BR SO BOM List", doc=doc, throw=True)
        det = _sort_bom_list_detail_rows(getattr(doc, "details", None))
        it, ct, pk = _split_stored_bom_detail_rows(det)
        all_item_rows.extend(it)
        all_carton_rows.extend(ct)
        all_pack_rows.extend(pk)
    return all_item_rows, all_carton_rows, all_pack_rows, missing


@frappe.whitelist()
def get_product_bom_list_new(sales_order_name=None, item_code=None):
    """
    与 get_product_bom_list 返回结构相同（header、items、cartonItems、packagingItems），
    数据来自已同步的 **BR SO BOM List / BR SO BOM List Details**，不再实时展开 BOM。

    入参、权限与 get_product_bom_list 一致。若对应主表记录不存在（尚未同步），返回失败说明。

    未传 item_code 时按销售订单明细行顺序合并多张 BR SO BOM List（与实时接口多行成品行为一致）。
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
        so_items = [si for si in so_items if (getattr(si, "item_code", None) or "").strip()]
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

        if item_code:
            so_items = [si for si in so_items if (getattr(si, "item_code", None) or "").strip() == item_code]
            if not so_items:
                return {
                    "success": False,
                    "message": "销售订单中未找到指定成品行: {}".format(item_code),
                }

        finished_codes = [(getattr(si, "item_code", None) or "").strip() for si in so_items]

        item_rows, carton_rows, pack_rows, missing = _load_flat_product_bom_rows_from_br_so_bom_list(
            sales_order_name, finished_codes
        )
        if missing:
            return {
                "success": False,
                "message": "以下成品尚未同步 BR SO BOM List（请保存销售订单触发同步后再试）: {}".format(
                    ", ".join(missing)
                ),
            }

        ig_names = []
        for r in item_rows + carton_rows + pack_rows:
            ig = (getattr(r, "item_group", None) or "").strip()
            if ig:
                ig_names.append(ig)
        ig_parent_cache = _get_item_group_parent_map(ig_names)

        items = [
            _detail_row_to_product_bom_api_row(r, i + 1, ig_parent_cache)
            for i, r in enumerate(item_rows)
        ]
        carton_items = [
            _detail_row_to_product_bom_api_row(r, i + 1, ig_parent_cache)
            for i, r in enumerate(carton_rows)
        ]
        packaging_items = [
            _detail_row_to_product_bom_api_row(r, i + 1, ig_parent_cache)
            for i, r in enumerate(pack_rows)
        ]

        total_cost = sum(flt(r.get("orderCost") or 0) for r in items)
        total_qty = sum(
            flt(getattr(si, "qty", None) or getattr(si, "stock_qty", None) or 0) for si in so_items
        )
        unit_estimated_cost = round(total_cost / total_qty, 4) if total_qty else None

        header = _build_header(so_doc, so_items)
        header["status"] = _resolve_br_so_bom_header_status(
            sales_order_name,
            finished_codes,
            header.get("status"),
        )
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
            title="get_product_bom_list_new",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}
