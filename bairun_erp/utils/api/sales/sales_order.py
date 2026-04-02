# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
销售合同保存接口：将前端提交的 order_data 保存为 ERPNext Sales Order。

- 不创建新 Item，items 中的 item_code 必须已存在
- 支持 json_data 包装和 FormData 传入
- 遵循 ERPNext 标准文档创建流程

bench execute 示例:
    bench --site site1.local execute bairun_erp.utils.api.sales.sales_order.save_sales_order --kwargs '{"order_data": {...}}'
"""

from __future__ import unicode_literals

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


# Sales Order Item 允许的字段（从 ERPNext 子表映射）
_SO_ITEM_FIELDS = {
    "item_code", "item_name", "description", "qty", "rate", "amount",
    "uom", "stock_uom", "delivery_date", "remarks", "additional_notes",
    "bom_no", "warehouse", "price_list_rate", "default_qty",
    "variant_of", "variant_item_code", "item_group", "has_variants",
}


def _parse_order_data(order_data, kwargs):
    """解析 order_data：支持 json_data 包装和 FormData 字符串。"""
    if not order_data and kwargs.get("json_data"):
        jd = kwargs["json_data"]
        if isinstance(jd, dict):
            order_data = jd.get("order_data") or jd
        elif isinstance(jd, str):
            try:
                jd = json.loads(jd)
                order_data = jd.get("order_data") if isinstance(jd, dict) else None
            except json.JSONDecodeError:
                return None
        else:
            order_data = None

    if isinstance(order_data, str):
        try:
            order_data = json.loads(order_data)
        except json.JSONDecodeError:
            return None

    return order_data


def _validate_order_data(order_data):
    """校验 order_data 必填项，返回 (None, None) 表示成功，否则返回 (None, error_dict)。"""
    if not order_data or not isinstance(order_data, dict):
        return None, {"error": _("Invalid input format. Expected dict or JSON string.")}

    if order_data.get("doctype") != "Sales Order":
        return None, {"error": _("Invalid doctype specified. Must be 'Sales Order'.")}

    # customer
    customer = order_data.get("customer")
    if not customer:
        return None, {"error": _("客户不能为空")}
    if not frappe.db.exists("Customer", customer):
        return None, {"error": _("客户 '{0}' 不存在").format(customer)}

    # company
    company = order_data.get("company")
    if not company:
        return None, {"error": _("公司名称不能为空")}
    if not frappe.db.exists("Company", company):
        return None, {"error": _("公司 '{0}' 不存在").format(company)}

    # items
    items = order_data.get("items") or []
    if not items:
        return None, {"error": _("至少需要一个产品明细")}

    for i, item in enumerate(items):
        idx = i + 1
        item_code = item.get("item_code")
        if not item_code:
            return None, {"error": _("产品明细第 {0} 行物料编码不能为空").format(idx)}
        if not frappe.db.exists("Item", item_code):
            return None, {"error": _("物料 '{0}' 不存在").format(item_code)}

        qty = item.get("qty")
        if qty is None or flt(qty) <= 0:
            return None, {"error": _("产品明细第 {0} 行数量必须大于 0").format(idx)}

        rate = item.get("rate")
        if rate is None or (isinstance(rate, (int, float)) and flt(rate) < 0):
            return None, {"error": _("产品明细第 {0} 行单价无效").format(idx)}

        if not item.get("uom"):
            return None, {"error": _("产品明细第 {0} 行单位不能为空").format(idx)}

    # transaction_date 格式
    txn_date = order_data.get("transaction_date")
    if txn_date:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(txn_date).strip()):
            return None, {"error": _("交易日期格式错误，需 YYYY-MM-DD")}
        try:
            getdate(txn_date)
        except Exception:
            return None, {"error": _("交易日期格式错误，需 YYYY-MM-DD")}

    # cost_center 若传则须有效
    cc = order_data.get("cost_center")
    if cc and not frappe.db.exists("Cost Center", cc):
        return None, {"error": _("成本中心 '{0}' 不存在").format(cc)}

    return order_data, None


def _to_sales_order_item(row):
    """将前端 item 行转为 Sales Order Item 结构，仅保留允许字段。"""
    out = {"doctype": "Sales Order Item"}
    for k, v in row.items():
        if k in _SO_ITEM_FIELDS and v is not None and v != "":
            out[k] = v
    if "doctype" in row and row.get("doctype") == "Item":
        pass  # 忽略 Item doctype，已设为 Sales Order Item
    return out


def _warehouse_usable_for_company(warehouse, company):
    """仓库存在、非组、未禁用，且所属公司与单据公司一致。"""
    if not warehouse or not company:
        return False
    row = frappe.db.get_value(
        "Warehouse",
        warehouse,
        ["disabled", "company", "is_group"],
        as_dict=True,
    )
    if not row or row.is_group or cint(row.disabled):
        return False
    return (row.company or "") == company


def _get_any_usable_warehouse(company):
    """最后兜底：该公司下任意可用（非组、未禁用）仓库，按名称稳定排序。"""
    wh = frappe.get_all(
        "Warehouse",
        filters={"company": company, "is_group": 0, "disabled": 0},
        fields=["name"],
        order_by="name asc",
        limit=1,
    )
    return wh[0].name if wh else None


def _resolve_warehouse_for_line(item_code, company):
    """
    未传 warehouse 时的解析顺序（与界面从 Item Defaults 带出一致）：
    1) Item 在公司下的 default_warehouse（tabItem Default）
    2) 全局 Stock Settings.default_warehouse（须属于该公司且未禁用）
    3) 该公司任意可用仓库

    注意：旧实现用 get_all(..., limit=1) 无排序、未过滤 disabled，可能落到已禁用仓（如「仓库 - B」）。
    """
    from erpnext.stock.doctype.item.item import get_item_defaults

    defaults = get_item_defaults(item_code, company) or {}
    wh = defaults.get("default_warehouse")
    if wh and _warehouse_usable_for_company(wh, company):
        return wh

    wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    if wh and _warehouse_usable_for_company(wh, company):
        return wh

    return _get_any_usable_warehouse(company)


def _prepare_order_doc(order_data):
    """将 order_data 转为可传入 frappe.get_doc 的字典。"""
    company = order_data.get("company")

    # items（先解析行仓库，便于 set_warehouse 与明细一致）
    items = []
    first_resolved_wh = None
    for row in order_data.get("items") or []:
        so_item = _to_sales_order_item(row)
        if not so_item.get("item_code"):
            continue
        if not so_item.get("warehouse"):
            wh = _resolve_warehouse_for_line(so_item["item_code"], company)
            if wh:
                so_item["warehouse"] = wh
                if not first_resolved_wh:
                    first_resolved_wh = wh
        items.append(so_item)

    doc = {
        "doctype": "Sales Order",
        "customer": order_data.get("customer"),
        "company": company,
        "order_type": order_data.get("order_type") or "Sales",
        "transaction_date": order_data.get("transaction_date"),
        "delivery_date": order_data.get("delivery_date"),
        "currency": order_data.get("currency") or "CNY",
        "conversion_rate": flt(order_data.get("conversion_rate"), 1) or 1,
        "status": order_data.get("status") or "Draft",
        "po_no": order_data.get("po_no") or "",
        "cost_center": order_data.get("cost_center") or "",
        "selling_price_list": order_data.get("selling_price_list") or "标准销售",
        "price_list_currency": order_data.get("price_list_currency") or "CNY",
        "plc_conversion_rate": flt(order_data.get("plc_conversion_rate"), 1) or 1,
        "set_warehouse": order_data.get("set_warehouse") or first_resolved_wh or "",
    }

    # 自定义字段（若 DocType 存在）
    for f in ("custom_material_code_display", "custom_style_number", "custom_sub_order_type", "custom_业务类型_", "business_type"):
        if order_data.get(f) is not None:
            doc[f] = order_data[f]

    doc["items"] = items

    # taxes
    taxes = order_data.get("taxes") or []
    tax_rows = []
    for t in taxes:
        if not isinstance(t, dict):
            continue
        tax_row = {
            "doctype": "Sales Taxes and Charges",
            "charge_type": t.get("charge_type") or "Actual",
            "account_head": t.get("account_head") or "",
            "description": t.get("description") or "",
            "tax_amount": flt(t.get("tax_amount"), 0),
        }
        if tax_row["account_head"] or tax_row["description"] or tax_row["tax_amount"]:
            tax_rows.append(tax_row)
    doc["taxes"] = tax_rows

    # name（更新场景）
    if order_data.get("name"):
        doc["name"] = order_data["name"]

    return doc


@frappe.whitelist(allow_guest=False)
def save_sales_order(order_data=None, *args, **kwargs):
    """
    保存销售合同（Sales Order）。

    入参:
        order_data: 销售订单数据 dict，或通过 json_data 传入。
        支持:
          - { "order_data": {...} }
          - { "json_data": { "order_data": {...} } }
          - FormData 时 order_data 为 JSON 字符串

    返回:
        成功: { "data": { "success": True, "name": "SAL-ORD-xxx", "message": "...", ... } }
        失败: { "error": "错误信息" }
    """
    try:
        if not order_data and args and isinstance(args[0], (str, dict)):
            order_data = args[0]
        elif not order_data and kwargs:
            order_data = _parse_order_data(None, kwargs)
        else:
            order_data = _parse_order_data(order_data, kwargs)

        order_data, err = _validate_order_data(order_data)
        if err:
            return err

        doc_dict = _prepare_order_doc(order_data)

        is_update = bool(order_data.get("name") and frappe.db.exists("Sales Order", order_data["name"]))

        if is_update:
            so = frappe.get_doc("Sales Order", order_data["name"])
            doc_dict.pop("name", None)  # 避免 overwrite name
            so.update(doc_dict)
            so.save(ignore_permissions=True)
        else:
            if doc_dict.get("name"):
                doc_dict["flags"] = {"ignore_naming_series": True}
            so = frappe.get_doc(doc_dict)
            so.insert(ignore_permissions=True)
            so.save(ignore_permissions=True)

        # 保存成功后同步产品物料清单到 BR SO BOM List / BR SO BOM List Details
        try:
            from bairun_erp.utils.api.sales.sales_order_bom_sync import (
                sync_bom_list_for_sales_order,
            )
            sync_bom_list_for_sales_order(so)
        except Exception:
            frappe.log_error(
                title="BOM sync after save_sales_order",
                message=frappe.get_traceback(),
            )

        frappe.db.commit()

        return {
            "data": {
                "success": True,
                "name": so.name,
                "message": _("销售订单保存成功"),
                "production_order_name": None,
                "rg_pattern_name": None,
            }
        }
    except frappe.ValidationError as e:
        frappe.db.rollback()
        return {"error": str(e)}
    except Exception as e:
        frappe.db.rollback()
        return {"error": str(e)}
