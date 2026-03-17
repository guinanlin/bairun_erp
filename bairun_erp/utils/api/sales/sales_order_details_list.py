# Copyright (c) 2025, Bairun and contributors
# 销售明细列表白名单接口：按筛选与分页返回 Sales Order 明细列表，供前端销售明细页调用。
#
# 方法路径: bairun_erp.utils.api.sales.sales_order_details_list.get_sales_order_details_list
# 请求方式: POST，Content-Type: application/json，body 为 json_data
#
# bench execute 示例:
#   bench --site site2.local execute bairun_erp.utils.api.sales.sales_order_details_list.get_sales_order_details_list --kwargs '{"limit_start": 0, "limit_page_length": 20}'

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import flt


# 主表列表所需字段（标准 + 可选自定义）
_SO_DETAILS_LIST_FIELDS = [
    "name",
    "transaction_date",
    "creation",
    "customer",
    "customer_name",
    "po_no",
    "currency",
    "grand_total",
    "total_qty",
    "docstatus",
    "status",
    "owner",
    "delivery_date",
    "per_billed",
    "per_delivered",
]
# 主表可能存在的自定义/扩展字段（若 meta 无则跳过）
_SO_OPTIONAL_FIELDS = (
    "custom_contract_no",
    "custom_style_number",
    "billing_status",
    "custom_sales_qty",
    "custom_marketing_fee",
    "custom_sub_order_type",
)


def _parse_params(kwargs):
    """从 kwargs 或 json_data 解析分页与筛选参数。"""
    params = {
        "limit_start": 0,
        "limit_page_length": 20,
        "order_by": "creation desc",
        "customer_name_search": None,
        "order_date_from": None,
        "order_date_to": None,
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

    params["limit_start"] = jd.get("limit_start", 0)
    params["limit_page_length"] = jd.get("limit_page_length", 20)
    params["order_by"] = jd.get("order_by") or "creation desc"
    params["customer_name_search"] = (jd.get("customer_name_search") or "").strip() or None
    params["order_date_from"] = (jd.get("order_date_from") or "").strip() or None
    params["order_date_to"] = (jd.get("order_date_to") or "").strip() or None
    return params


def _get_so_meta_fieldnames():
    """返回 Sales Order 主表实际存在的字段名集合（含自定义）。"""
    meta = frappe.get_meta("Sales Order")
    return {f.fieldname for f in meta.get("fields")}


def _get_item_aggregates(so_names):
    """
    批量获取每个 Sales Order 的 item_name 汇总（顿号拼接）、
    子表 delivered_qty 汇总、总订单数量（用于计算未交数量）。
    返回: (item_names_by_so, delivered_qty_by_so, total_qty_by_so)
    """
    if not so_names:
        return {}, {}, {}

    rows = frappe.db.sql(
        """
        SELECT parent, item_name, qty, IFNULL(delivered_qty, 0) AS delivered_qty
        FROM `tabSales Order Item`
        WHERE parent IN %s
        ORDER BY parent, idx
        """,
        [so_names],
        as_dict=True,
    )

    item_names_by_so = {}
    delivered_qty_by_so = {}
    total_qty_by_so = {}

    for r in rows:
        parent = r.get("parent")
        if not parent:
            continue
        names = item_names_by_so.setdefault(parent, [])
        item_name = (r.get("item_name") or "").strip()
        if item_name and item_name not in names:
            names.append(item_name)
        delivered_qty_by_so[parent] = delivered_qty_by_so.get(parent, 0) + flt(r.get("delivered_qty"))
        total_qty_by_so[parent] = total_qty_by_so.get(parent, 0) + flt(r.get("qty"))

    for k in list(item_names_by_so.keys()):
        item_names_by_so[k] = "、".join(item_names_by_so[k]) if item_names_by_so[k] else None

    return item_names_by_so, delivered_qty_by_so, total_qty_by_so


@frappe.whitelist(allow_guest=False)
def get_sales_order_details_list(**kwargs):
    """
    销售明细列表白名单接口。

    请求体（POST json_data）:
        limit_start: int（可选），分页偏移，默认 0
        limit_page_length: int（可选），每页条数，默认 20
        order_by: str（可选），排序，默认 "creation desc"
        customer_name_search: str（可选），客户名称模糊搜索
        order_date_from: str（可选），下单日期起 YYYY-MM-DD
        order_date_to: str（可选），下单日期止 YYYY-MM-DD

    返回: { "data": [ {...}, ... ], "total": N }
    单条记录包含主表字段、item_names（子表物料名拼接）、delivered_qty（子表已交汇总）、
    outstanding_qty（总数量 - 已交数量），以及若存在的自定义字段。
    """
    params = _parse_params(kwargs)
    limit_start = int(params["limit_start"])
    limit_page_length = params["limit_page_length"]
    try:
        limit_page_length = int(limit_page_length)
    except (TypeError, ValueError):
        limit_page_length = 20
    if limit_page_length <= 0:
        limit_page_length = 20

    filters = [["docstatus", "<", 2]]  # 排除 Cancel
    or_filters = []

    search_customer = (params.get("customer_name_search") or "").strip()
    if search_customer:
        or_filters.append(["Sales Order", "customer", "like", "%" + search_customer + "%"])
        or_filters.append(["Sales Order", "customer_name", "like", "%" + search_customer + "%"])

    order_date_from = params.get("order_date_from")
    order_date_to = params.get("order_date_to")
    if order_date_from:
        filters.append(["transaction_date", ">=", order_date_from])
    if order_date_to:
        filters.append(["transaction_date", "<=", order_date_to])

    meta_fields = _get_so_meta_fieldnames()
    requested = ["name"]
    for f in _SO_DETAILS_LIST_FIELDS:
        if f in meta_fields and f not in requested:
            requested.append(f)
    for f in _SO_OPTIONAL_FIELDS:
        if f in meta_fields and f not in requested:
            requested.append(f)

    order_by = (params.get("order_by") or "creation desc").strip()
    # 安全：仅允许已知列名与 asc/desc
    allowed_order_cols = {"creation", "modified", "transaction_date", "delivery_date", "grand_total", "name"}
    parts = order_by.split()
    if not parts or parts[0].lower() not in allowed_order_cols:
        order_by = "creation desc"
    elif len(parts) == 1:
        order_by = "{} desc".format(parts[0])
    else:
        order_by = "{} {}".format(parts[0], "desc" if parts[1].lower() == "desc" else "asc")

    so_list = frappe.get_list(
        "Sales Order",
        fields=requested,
        filters=filters,
        or_filters=or_filters if or_filters else None,
        order_by=order_by,
        limit_start=limit_start,
        limit_page_length=limit_page_length,
        ignore_permissions=False,
    )

    # 使用相同筛选条件取总数（仅取 name，limit_page_length=0 表示不限制条数）
    total_list = frappe.get_list(
        "Sales Order",
        fields=["name"],
        filters=filters,
        or_filters=or_filters if or_filters else None,
        limit_page_length=0,
        ignore_permissions=False,
    )
    total = len(total_list)

    so_names = [r.get("name") for r in so_list if r.get("name")]
    item_names_by_so, delivered_qty_by_so, total_qty_by_so = _get_item_aggregates(so_names)

    data = []
    for r in so_list:
        so_name = r.get("name")
        row = dict(r)
        row["item_names"] = item_names_by_so.get(so_name)
        total_qty = flt(row.get("total_qty")) or total_qty_by_so.get(so_name) or 0
        delivered_qty = delivered_qty_by_so.get(so_name) or 0
        row["delivered_qty"] = delivered_qty
        row["outstanding_qty"] = max(0, total_qty - delivered_qty)
        data.append(row)

    return {"data": data, "total": total}
