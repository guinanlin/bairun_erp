# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
BOM 查询接口：根据 BOM name 获取物料明细或树形结构。

- get_bom_items_for_contract: 扁平物料明细，用于销售合同「BOM 的物料明细」预填
- get_bom_tree_for_edit: 树形结构，用于 BOM 编辑页画布（MindMap）展示

bench execute 示例:
    bench --site site2.local execute bairun_erp.utils.api.material.bom_query.get_bom_items_for_contract --kwargs '{"bom_name": "BOM-A-01-001"}'
    bench --site site2.local execute bairun_erp.utils.api.material.bom_query.get_bom_tree_for_edit --kwargs '{"bom_name": "BOM-A-01-001"}'
"""

from __future__ import unicode_literals

import frappe


# Item 自定义/扩展字段映射：规范字段名 -> 系统字段名
_ITEM_EXTRA_FIELDS = [
    ("height", ["custom_height", "height"]),
    ("diameter", ["custom_diameter_width", "diameter_width"]),
    ("inner_cover_width", ["custom_inner_cover_width", "inner_cover_width"]),
    ("material", ["custom_material", "material"]),
]

# Item Variant Attribute 中 attribute 名称 -> 规范字段名（用于 color、part 等）
_ATTR_TO_FIELD = {
    "颜色": "color",
    "Color": "color",
    "colour": "color",
    "部位": "part",
    "Part": "part",
}


def _get_item_extra_fields(item_code):
    """获取 Item 的扩展字段（height、diameter、inner_cover_width、material）"""
    result = {}
    meta = frappe.get_meta("Item")
    fields_to_get = ["description"]
    for _out, candidates in _ITEM_EXTRA_FIELDS:
        for f in candidates:
            if meta.get_field(f):
                fields_to_get.append(f)
                break
    item_values = frappe.db.get_value("Item", item_code, fields_to_get, as_dict=True) or {}

    for out_key, candidates in _ITEM_EXTRA_FIELDS:
        val = ""
        for f in candidates:
            if meta.get_field(f):
                v = item_values.get(f)
                if v is not None and v != "":
                    val = str(v)
                break
        result[out_key] = val

    desc = item_values.get("description")
    result["description"] = (desc or "").strip() if desc else ""
    return result


def _get_item_variant_attrs(item_codes):
    """批量获取 Item 的 Variant 属性（color、part 等）"""
    if not item_codes:
        return {}
    rows = frappe.get_all(
        "Item Variant Attribute",
        filters={"parent": ["in", item_codes]},
        fields=["parent", "attribute", "attribute_value"],
    )
    # 可能 attribute 存的是 Item Attribute 的 name，需要取 attribute_name
    attr_names = {}
    for r in rows:
        attr = r.get("attribute")
        if attr and attr not in attr_names:
            an = frappe.db.get_value("Item Attribute", attr, "attribute_name") or attr
            attr_names[attr] = an

    out = {}
    for r in rows:
        parent = r.get("parent")
        if not parent:
            continue
        if parent not in out:
            out[parent] = {}
        an = attr_names.get(r.get("attribute"), r.get("attribute") or "")
        for attr_label, field_name in _ATTR_TO_FIELD.items():
            if attr_label in (an, r.get("attribute")):
                out[parent][field_name] = r.get("attribute_value") or ""
                break
    return out


def _get_item_tree_fields(item_codes):
    """
    批量获取 Item 的 item_group、stock_uom、default_warehouse、default_supplier。
    返回 dict: item_code -> {item_group, stock_uom, warehouse, supplier}
    """
    if not item_codes:
        return {}
    item_codes = list(set(item_codes))
    meta = frappe.get_meta("Item")
    fields = ["name", "item_group", "stock_uom", "description"]
    if meta.get_field("default_warehouse"):
        fields.append("default_warehouse")
    if meta.get_field("default_supplier"):
        fields.append("default_supplier")

    rows = frappe.get_all(
        "Item",
        filters={"name": ["in", item_codes]},
        fields=fields,
    )
    result = {}
    for r in rows:
        ic = r.get("name")
        if not ic:
            continue
        result[ic] = {
            "item_group": (r.get("item_group") or "").strip(),
            "stock_uom": (r.get("stock_uom") or "").strip() or "Nos",
            "warehouse": (r.get("default_warehouse") or "").strip(),
            "supplier": (r.get("default_supplier") or "").strip(),
            "description": (r.get("description") or "").strip() if r.get("description") else "",
        }
    # 若 Item 无 default_supplier，尝试从 Item Supplier 取第一个
    for ic in item_codes:
        if ic not in result:
            result[ic] = {
                "item_group": "",
                "stock_uom": "Nos",
                "warehouse": "",
                "supplier": "",
                "description": "",
            }
        elif not result[ic]["supplier"] and frappe.db.exists("Item Supplier", {"parent": ic}):
            first = frappe.db.get_value(
                "Item Supplier",
                {"parent": ic},
                "supplier",
                order_by="idx asc",
            )
            if first:
                result[ic]["supplier"] = first
    return result


def _bom_item_to_tree_node(bi, bom_doc, item_details, parent_bom_qty=1):
    """
    将 BOM Item 转为树节点（不含递归子节点）。
    parent_bom_qty: 父 BOM 的 quantity，用于计算 bom_qty
    """
    item_code = bi.item_code or ""
    details = item_details.get(item_code, {})
    bom_qty = float(bi.stock_qty or bi.qty or 0) / float(parent_bom_qty or 1)

    warehouse = (getattr(bi, "source_warehouse", None) or "").strip() or details.get("warehouse", "")
    process = (getattr(bi, "operation", None) or "").strip()
    desc = (getattr(bi, "description", None) or "").strip() or details.get("description", "")

    node = {
        "id": bi.name,
        "item_code": item_code,
        "item_name": (bi.item_name or "").strip(),
        "bom_qty": round(bom_qty, 6),
        "children": [],
        "item_group": (details.get("item_group") or "").strip(),
    }
    if details.get("stock_uom"):
        node["stock_uom"] = details["stock_uom"]
    if process:
        node["process"] = process
    if warehouse:
        node["warehouse"] = warehouse
    if details.get("supplier"):
        node["supplier"] = details["supplier"]
    if desc:
        node["description"] = desc
    return node


def _build_bom_tree(bom_name, item_details_cache=None):
    """
    递归构建 BOM 树。返回根节点 dict，或 None 若 BOM 不存在。
    """
    if not frappe.db.exists("BOM", bom_name):
        return None
    bom_doc = frappe.get_doc("BOM", bom_name)
    root_item_code = bom_doc.item or ""
    if not root_item_code:
        return None

    if item_details_cache is None:
        item_details_cache = {}
    # 收集本层及子 BOM 涉及的 item_code
    codes_to_fetch = [root_item_code]
    for bi in (bom_doc.items or []):
        if bi.item_code:
            codes_to_fetch.append(bi.item_code)
        if bi.bom_no:
            sub_bom = frappe.get_cached_doc("BOM", bi.bom_no)
            if sub_bom and sub_bom.item:
                codes_to_fetch.append(sub_bom.item)
    for ic in codes_to_fetch:
        if ic and ic not in item_details_cache:
            item_details_cache[ic] = {}
    # 批量补全 item_details
    missing = [c for c in codes_to_fetch if c and not item_details_cache.get(c)]
    if missing:
        fetched = _get_item_tree_fields(missing)
        for k, v in fetched.items():
            item_details_cache[k] = v

    details = item_details_cache.get(root_item_code, {})
    parent_qty = float(bom_doc.quantity or 1)

    root = {
        "id": "root",
        "item_code": root_item_code,
        "item_name": (bom_doc.item_name or details.get("item_name") or "").strip(),
        "bom_qty": 1,
        "children": [],
        "item_group": (details.get("item_group") or "").strip(),
    }
    # 从 Item 取 item_name（BOM 可能没有 item_name 字段）
    if not root["item_name"]:
        root["item_name"] = frappe.db.get_value("Item", root_item_code, "item_name") or root_item_code
    if details.get("stock_uom"):
        root["stock_uom"] = details["stock_uom"]
    if details.get("warehouse"):
        root["warehouse"] = details["warehouse"]
    if details.get("supplier"):
        root["supplier"] = details["supplier"]
    if details.get("description"):
        root["description"] = details["description"]

    for bi in bom_doc.items or []:
        if bi.bom_no:
            sub_tree = _build_bom_tree(bi.bom_no, item_details_cache)
            if sub_tree:
                sub_tree["id"] = bi.name
                sub_tree["bom_qty"] = round(
                    float(bi.stock_qty or bi.qty or 0) / parent_qty, 6
                )
                # 子 BOM 根节点继承父 BOM Item 的 source_warehouse，用于 inventoryQty 取数
                _wh = (getattr(bi, "source_warehouse", None) or "").strip()
                if _wh:
                    sub_tree["warehouse"] = _wh
                root["children"].append(sub_tree)
            else:
                fallback = _bom_item_to_tree_node(bi, bom_doc, item_details_cache, parent_qty)
                root["children"].append(fallback)
        else:
            child = _bom_item_to_tree_node(bi, bom_doc, item_details_cache, parent_qty)
            root["children"].append(child)

    return root


@frappe.whitelist()
def get_bom_tree_for_edit(bom_name=None):
    """
    根据 BOM name 获取树形结构，用于 BOM 编辑页画布（MindMap）展示。

    入参:
        bom_name (str): BOM 的 name，如 BOM-成品_mm312dcx8o8w-001

    返回:
        success=True: {"success": true, "data": {"bom_name": "...", "tree": {...}}}
        success=False: {"success": false, "message": "..."}

    tree 为根节点，含 id, item_code, item_name, bom_qty, children,
    及可选 item_group, stock_uom, process, warehouse, supplier, description
    """
    bom_name = (bom_name or "").strip()
    if not bom_name:
        return {"success": False, "message": "bom_name 不能为空"}

    try:
        if not frappe.db.exists("BOM", bom_name):
            return {"success": False, "message": "BOM 不存在或无权访问"}

        bom_doc = frappe.get_doc("BOM", bom_name)
        frappe.has_permission("BOM", doc=bom_doc, throw=True)

        tree = _build_bom_tree(bom_name)
        if not tree:
            return {"success": False, "message": "BOM 不存在或无权访问"}

        return {
            "success": True,
            "data": {"bom_name": bom_name, "tree": tree},
        }

    except frappe.PermissionError:
        return {"success": False, "message": "BOM 不存在或无权访问"}
    except Exception as e:
        frappe.log_error(
            title="get_bom_tree_for_edit",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_bom_items_for_contract(bom_name=None):
    """
    根据 BOM name 获取物料明细，用于 BOM 生单时预填销售合同的「BOM 的物料明细」表格。

    入参:
        bom_name (str): BOM 的 name，如 BOM-成品_mm312dcx8o8w-001

    返回:
        success=True: {"success": true, "data": {"bom_name": "...", "items": [...]}}
        success=False: {"success": false, "message": "..."}

    items 每项含: item_code, item_name, qty, uom, rate, amount, description,
    color, part, height, diameter, inner_cover_width, material
    """
    bom_name = (bom_name or "").strip()
    if not bom_name:
        return {"success": False, "message": "bom_name 不能为空"}

    try:
        if not frappe.db.exists("BOM", bom_name):
            return {"success": False, "message": "BOM 不存在或无权访问"}

        bom_doc = frappe.get_doc("BOM", bom_name)
        frappe.has_permission("BOM", doc=bom_doc, throw=True)

        bom_items = bom_doc.items or []
        if not bom_items:
            return {
                "success": True,
                "data": {"bom_name": bom_name, "items": []},
            }

        item_codes = [bi.item_code for bi in bom_items if bi.item_code]
        item_extras = {}
        for ic in item_codes:
            item_extras[ic] = _get_item_extra_fields(ic)

        variant_attrs = _get_item_variant_attrs(item_codes)

        items = []
        for bi in bom_items:
            ic = bi.item_code or ""
            extra = item_extras.get(ic, {})
            attrs = variant_attrs.get(ic, {})

            qty = float(bi.qty or 0)
            rate = float(bi.rate or 0)
            amount = float(bi.amount or 0)
            if amount == 0 and qty and rate:
                amount = qty * rate

            row = {
                "item_code": ic,
                "item_name": (bi.item_name or "").strip(),
                "qty": qty,
                "uom": (bi.uom or "").strip() or "Nos",
                "rate": rate,
                "amount": amount,
                "description": (bi.description or "").strip() or extra.get("description", ""),
                "color": attrs.get("color", ""),
                "part": attrs.get("part", ""),
                "height": extra.get("height", ""),
                "diameter": extra.get("diameter", ""),
                "inner_cover_width": extra.get("inner_cover_width", ""),
                "material": extra.get("material", ""),
            }
            items.append(row)

        return {
            "success": True,
            "data": {"bom_name": bom_name, "items": items},
        }

    except frappe.PermissionError:
        return {"success": False, "message": "BOM 不存在或无权访问"}
    except Exception as e:
        frappe.log_error(
            title="get_bom_items_for_contract",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}
