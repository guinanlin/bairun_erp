# Copyright (c) 2026, Bairun and contributors
# 销售合同保存后，将产品物料清单同步写入 BR SO BOM List / BR SO BOM List Details。
# 仅通过标准 Frappe API 写入，不修改两 DocType 结构。

from __future__ import unicode_literals

import frappe
from frappe.utils import flt


def _get_customer_code_and_name(customer_link):
    """从 Customer 获取编号与名称（name 作编号；customer_name 作名称）。"""
    if not customer_link:
        return "", ""
    # 仅查标准列：Customer 表可能无 customer_code
    row = frappe.db.get_value(
        "Customer",
        customer_link,
        ["name", "customer_name"],
        as_dict=True,
    )
    if not row:
        return customer_link, customer_link
    code = (row.get("name") or "").strip() or customer_link
    name = (row.get("customer_name") or row.get("name") or "").strip() or customer_link
    return code, name


def _item_row_to_detail(row, row_no):
    """将 get_product_bom_list 返回的一行（items/cartonItems/packagingItems）转为 BR SO BOM List Details 行（snake_case）。"""
    return {
        "doctype": "BR SO BOM List Details",
        "row_no": row_no,
        "item_code": (row.get("itemCode") or row.get("item_code") or "").strip(),
        "level": row.get("level"),
        "bom_code": (row.get("bomCode") or row.get("bom_code") or "").strip() or None,
        "item_name": (row.get("itemName") or row.get("item_name") or "").strip(),
        "item_group": (row.get("item_group") or row.get("itemGroup") or "").strip() or None,
        "ratio_qty": flt(row.get("ratioQty") or row.get("ratio_qty"), 0),
        "required_qty_override": None,
        "inventory_qty": flt(row.get("inventoryQty") or row.get("inventory_qty")),
        "supplier_code": (row.get("supplierCode") or row.get("supplier_code") or "").strip() or None,
        "supplier_name": (row.get("supplierName") or row.get("supplier_name") or "").strip() or None,
        "process_name": (row.get("process") or row.get("process_name") or "").strip() or None,
        "estimated_cost": flt(row.get("estimatedCost") or row.get("estimated_cost")),
        "order_cost": flt(row.get("orderCost") or row.get("order_cost"), 0),
        "warehouse_code": (row.get("warehouseCode") or row.get("warehouse_code") or "").strip() or None,
        "warehouse_name": (row.get("warehouseName") or row.get("warehouse_name") or "").strip() or None,
        "warehouse_slot": (row.get("warehouseSlot") or row.get("warehouse_slot") or "").strip() or None,
        "order_status": (row.get("orderStatus") or row.get("order_status") or "未生单").strip(),
        "order_confirmation_status": (row.get("orderConfirmationStatus") or row.get("order_confirmation_status") or "").strip() or None,
        "received_qty": flt(row.get("receivedQty") or row.get("received_qty")),
        "unreceived_qty": flt(row.get("unreceivedQty") or row.get("unreceived_qty")),
        "loss_ratio": flt(row.get("lossRatio") or row.get("loss_ratio")),
        "purchase_order_no": (row.get("purchaseOrderNo") or row.get("purchase_order_no") or "").strip() or None,
    }


def _build_bom_list_doc(so_doc, header, item_code, items, carton_items, packaging_items):
    """
    组装 BR SO BOM List 主表 + details 子表（snake_case）。
    header 为 get_product_bom_list 返回的 camelCase；items/carton_items/packaging_items 为行列表。
    """
    order_no = (header.get("orderNo") or so_doc.name or "").strip()
    customer_code, customer_name = _get_customer_code_and_name(so_doc.get("customer"))

    main = {
        "doctype": "BR SO BOM List",
        "order_no": order_no,
        "status": (header.get("status") or "draft").strip(),
        "customer_code": customer_code,
        "customer_name": customer_name,
        "item_code": (item_code or "").strip(),
        "item_name": (header.get("itemName") or "").strip(),
        "delivery_date": so_doc.get("delivery_date"),
        "approved_by": None,
        "approved_on": None,
        "created_by": so_doc.get("owner") or so_doc.get("modified_by") or "",
        "project_no": (header.get("projectNo") or "").strip() or None,
        "order_qty": flt(header.get("orderQty"), 0),
        "sales_price": flt(header.get("salesPrice"), 0),
        "unit_estimated_cost": flt(header.get("unitEstimatedCost")),
        "running_cost_rate": flt(header.get("runningCostRate"), 0),
        "transport_fee_rate": flt(header.get("transportFeeRate"), 0),
        "tax_rate": flt(header.get("taxRate"), 0),
        "gross_margin": flt(header.get("grossMargin")),
        "warehouse_code": (header.get("warehouseCode") or "").strip() or None,
        "warehouse_name": (header.get("warehouseName") or "").strip() or None,
        "inventory_qty": flt(header.get("inventoryQty")),
        "details": [],
    }

    # 合并 items + cartonItems + packagingItems。
    # 业务约束：配比为 0 的物料不进入明细；其余行再做连续编号。
    all_rows = (items or []) + (carton_items or []) + (packaging_items or [])
    seen_keys = set()
    row_no = 1
    for row in all_rows:
        item_code = (row.get("itemCode") or row.get("item_code") or "").strip()
        if not item_code:
            continue
        ratio_qty = flt(row.get("ratioQty") or row.get("ratio_qty"), 0)
        if ratio_qty == 0:
            continue
        bom_code = (row.get("bomCode") or row.get("bom_code") or "").strip()
        level = row.get("level")
        dedupe_key = (item_code, bom_code, level)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        main["details"].append(_item_row_to_detail(row, row_no))
        row_no += 1

    return main


def _save_bom_list_doc(doc_dict):
    """插入或更新 BR SO BOM List（含 details），不抛异常时返回 True。"""
    order_no = (doc_dict.get("order_no") or "").strip()
    item_code = (doc_dict.get("item_code") or "").strip()
    if not order_no or not item_code:
        return False
    name = "{}-{}".format(order_no, item_code)
    exists = frappe.db.exists("BR SO BOM List", name)
    if exists:
        doc = frappe.get_doc("BR SO BOM List", name)
        for key, value in doc_dict.items():
            if key == "details":
                doc.details = []
                for detail in value:
                    doc.append("details", detail)
            elif key != "doctype" and hasattr(doc, key):
                doc.set(key, value)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc(doc_dict)
        doc.insert(ignore_permissions=True)
        doc.save(ignore_permissions=True)
    return True


def sync_bom_list_for_sales_order(so_doc):
    """
    销售合同保存后调用：按 SO 每行成品调用 get_product_bom_list，并写入 BR SO BOM List + Details。
    单行失败仅打日志，不抛异常，不改变保存接口返回。
    """
    if not so_doc or not getattr(so_doc, "items", None):
        return
    from bairun_erp.utils.api.sales.sales_order_query_bom_details import get_product_bom_list

    for so_item in so_doc.items:
        item_code = (getattr(so_item, "item_code", None) or "").strip()
        if not item_code:
            continue
        try:
            result = get_product_bom_list(
                sales_order_name=so_doc.name,
                item_code=item_code,
            )
            if not result or not result.get("success") or not result.get("data"):
                frappe.log_error(
                    message="get_product_bom_list failed or empty for {} / {}".format(
                        so_doc.name, item_code
                    ),
                    title="BOM sync get_product_bom_list",
                )
                continue
            data = result["data"]
            header = data.get("header") or {}
            items = data.get("items") or []
            carton_items = data.get("cartonItems") or []
            packaging_items = data.get("packagingItems") or []

            doc_dict = _build_bom_list_doc(
                so_doc, header, item_code, items, carton_items, packaging_items
            )
            _save_bom_list_doc(doc_dict)
        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title="BOM sync BR SO BOM List",
            )
