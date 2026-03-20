from __future__ import unicode_literals

import json

import frappe
from frappe.utils import cint, flt, now_datetime


def _parse_kwargs_json_data(kwargs):
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


def _to_float_or_none(value):
    if value in (None, ""):
        return None
    return flt(value)


def _validate_percent(name, value, minimum=0, maximum=100):
    if value is None:
        return
    f = flt(value)
    if f < minimum or f > maximum:
        frappe.throw("{} 超出范围，必须在 {}~{} 之间".format(name, minimum, maximum))


def _normalize_detail_row(row):
    if not isinstance(row, dict):
        frappe.throw("details 必须是对象数组")

    # name：子表行主键；查询接口 get_product_bom_list_new 常以 id 回传同一值
    _row_name = _pick(row, "name", "id")
    out = {
        "name": (_row_name or "").strip() or None,
        "row_no": _pick(row, "row_no", "rowNo"),
        "item_code": (_pick(row, "item_code", "itemCode") or "").strip(),
        "level": _pick(row, "level"),
        "bom_code": (_pick(row, "bom_code", "bomCode") or "").strip() or None,
        "item_name": (_pick(row, "item_name", "itemName") or "").strip(),
        "item_group": (_pick(row, "item_group", "itemGroup") or "").strip() or None,
        "ratio_qty": _to_float_or_none(_pick(row, "ratio_qty", "ratioQty")),
        "required_qty_override": _to_float_or_none(_pick(row, "required_qty_override", "requiredQtyOverride")),
        "inventory_qty": _to_float_or_none(_pick(row, "inventory_qty", "inventoryQty")),
        "supplier_code": (_pick(row, "supplier_code", "supplierCode") or "").strip() or None,
        "supplier_name": (_pick(row, "supplier_name", "supplierName") or "").strip() or None,
        "process_name": (_pick(row, "process_name", "processName", "process") or "").strip() or None,
        "estimated_cost": _to_float_or_none(_pick(row, "estimated_cost", "estimatedCost")),
        "order_cost": _to_float_or_none(_pick(row, "order_cost", "orderCost")),
        "warehouse_code": (_pick(row, "warehouse_code", "warehouseCode") or "").strip() or None,
        "warehouse_name": (_pick(row, "warehouse_name", "warehouseName") or "").strip() or None,
        "warehouse_slot": (_pick(row, "warehouse_slot", "warehouseSlot") or "").strip() or None,
        "order_status": (_pick(row, "order_status", "orderStatus") or "").strip() or None,
        "order_confirmation_status": (_pick(row, "order_confirmation_status", "orderConfirmationStatus") or "").strip() or None,
        "received_qty": _to_float_or_none(_pick(row, "received_qty", "receivedQty")),
        "unreceived_qty": _to_float_or_none(_pick(row, "unreceived_qty", "unreceivedQty")),
        "loss_ratio": _to_float_or_none(_pick(row, "loss_ratio", "lossRatio")),
        "purchase_order_no": (_pick(row, "purchase_order_no", "purchaseOrderNo") or "").strip() or None,
    }

    if out["item_code"] == "":
        frappe.throw("明细 item_code 不能为空")

    if out["row_no"] not in (None, ""):
        out["row_no"] = cint(out["row_no"])
    else:
        out["row_no"] = None
    if out["level"] not in (None, ""):
        out["level"] = cint(out["level"])
    else:
        out["level"] = None

    return out


def _detail_business_key(d):
    return (
        cint(d.get("row_no")) if d.get("row_no") is not None else None,
        (d.get("item_code") or "").strip(),
        (d.get("bom_code") or "").strip(),
        cint(d.get("level")) if d.get("level") is not None else None,
    )


@frappe.whitelist(allow_guest=False)
def audit_so_bom_list(**kwargs):
    """
    审核并保存 BR SO BOM List（主表）与 BR SO BOM List Details（子表，按行 upsert）。

    入参支持直接字段或 json_data（dict / json string）:
        - sales_order_no / order_no: 必填
        - item_code: 必填（成品编码）
        - header: 可选，主表审核字段
        - details: 可选，明细数组；支持新增 + 更新
        - mark_approved: 可选，默认 1；1 时将 status 置为 approved 并补审核人/时间
    """
    jd = _parse_kwargs_json_data(kwargs)

    order_no = (_pick(jd, "sales_order_no", "order_no", "salesOrderNo", "orderNo") or "").strip()
    item_code = (_pick(jd, "item_code", "itemCode") or "").strip()
    if not order_no:
        return {"success": False, "message": "sales_order_no 不能为空"}
    if not item_code:
        return {"success": False, "message": "item_code 不能为空"}

    docname = "{}-{}".format(order_no, item_code)
    if not frappe.db.exists("BR SO BOM List", docname):
        return {"success": False, "message": "未找到对应 BOM 清单: {}".format(docname)}

    try:
        doc = frappe.get_doc("BR SO BOM List", docname)
        frappe.has_permission("BR SO BOM List", doc=doc, ptype="write", throw=True)

        header = jd.get("header") or {}
        if not isinstance(header, dict):
            header = {}

        running_cost_rate = _pick(header, "running_cost_rate", "runningCostRate")
        transport_fee_rate = _pick(header, "transport_fee_rate", "transportFeeRate")
        tax_rate = _pick(header, "tax_rate", "taxRate")
        gross_margin = _pick(header, "gross_margin", "grossMargin")
        approved_by = _pick(header, "approved_by", "approvedBy")
        approved_on = _pick(header, "approved_on", "approvedOn")
        status = _pick(header, "status")

        _validate_percent("running_cost_rate", _to_float_or_none(running_cost_rate))
        _validate_percent("transport_fee_rate", _to_float_or_none(transport_fee_rate))
        _validate_percent("tax_rate", _to_float_or_none(tax_rate), minimum=0, maximum=1000)
        _validate_percent("gross_margin", _to_float_or_none(gross_margin), minimum=-1000, maximum=1000)

        if running_cost_rate not in (None, ""):
            doc.running_cost_rate = flt(running_cost_rate)
        if transport_fee_rate not in (None, ""):
            doc.transport_fee_rate = flt(transport_fee_rate)
        if tax_rate not in (None, ""):
            doc.tax_rate = flt(tax_rate)
        if gross_margin not in (None, ""):
            doc.gross_margin = flt(gross_margin)
        if status not in (None, ""):
            doc.status = (status or "").strip()

        # 明细 upsert（仅新增 + 更新，不删除未传行）
        payload_details = jd.get("details")
        if payload_details is None:
            payload_details = []
        if not isinstance(payload_details, list):
            return {"success": False, "message": "details 必须为数组"}

        normalized = [_normalize_detail_row(r) for r in payload_details]

        existing_by_name = {}
        existing_by_key = {}
        max_row_no = 0
        for row in (doc.details or []):
            if row.name:
                existing_by_name[row.name] = row
            max_row_no = max(max_row_no, cint(getattr(row, "row_no", 0)))
            key = _detail_business_key({
                "row_no": getattr(row, "row_no", None),
                "item_code": getattr(row, "item_code", None),
                "bom_code": getattr(row, "bom_code", None),
                "level": getattr(row, "level", None),
            })
            if key not in existing_by_key:
                existing_by_key[key] = row

        for d in normalized:
            target = None
            if d.get("name") and d["name"] in existing_by_name:
                target = existing_by_name[d["name"]]
            else:
                key = _detail_business_key(d)
                target = existing_by_key.get(key)

            if target:
                for field, value in d.items():
                    if field == "name":
                        continue
                    if value is not None:
                        setattr(target, field, value)
                continue

            if d.get("row_no") is None:
                max_row_no += 1
                d["row_no"] = max_row_no
            d["doctype"] = "BR SO BOM List Details"
            d.pop("name", None)
            doc.append("details", d)

        mark_approved = cint(_pick(jd, "mark_approved", "markApproved"), 1)
        if mark_approved:
            doc.status = "approved"
            doc.approved_by = (approved_by or frappe.session.user or "").strip()
            doc.approved_on = approved_on or now_datetime()
        else:
            if approved_by not in (None, ""):
                doc.approved_by = (approved_by or "").strip()
            if approved_on not in (None, ""):
                doc.approved_on = approved_on

        doc.save()
        frappe.db.commit()

        return {
            "success": True,
            "message": "审核保存成功",
            "data": {
                "name": doc.name,
                "order_no": doc.order_no,
                "item_code": doc.item_code,
                "status": doc.status,
                "approved_by": doc.approved_by,
                "approved_on": doc.approved_on,
                "details_count": len(doc.details or []),
            },
        }
    except frappe.PermissionError:
        return {"success": False, "message": "无权限审核该 BOM 清单"}
    except Exception as e:
        frappe.log_error(
            title="audit_so_bom_list",
            message=frappe.get_traceback(),
        )
        return {"success": False, "message": str(e)}
