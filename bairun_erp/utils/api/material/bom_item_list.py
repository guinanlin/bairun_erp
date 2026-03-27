from __future__ import unicode_literals

import json

import frappe
from frappe.utils import cint, flt, now_datetime

# 主表 status 常用值（Doctype 为 Data，仍可由接口/同步写入其它字符串）
SO_BOM_LIST_STATUS_DRAFT = "draft"  # 销售订单保存触发 BOM 同步时的默认值（见 sales_order_bom_sync）
SO_BOM_LIST_STATUS_SAVED = "saved"  # save_so_bom_list 默认
SO_BOM_LIST_STATUS_APPROVED = "approved"  # audit_so_bom_list（mark_approved=1）默认
# 一键生单（save_purchase_orders 等）回写明细后，当主表下全部明细均已关联采购单号时写入主表
SO_BOM_LIST_STATUS_PO_RAISED = "po_raised"  # 前端展示：已生单 / 已升单（与明细 order_status「已生单」对应）


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


def _audit_resolve_docname(jd):
    order_no = (_pick(jd, "sales_order_no", "order_no", "salesOrderNo", "orderNo") or "").strip()
    item_code = (_pick(jd, "item_code", "itemCode") or "").strip()
    if not order_no:
        return None, "sales_order_no 不能为空"
    if not item_code:
        return None, "item_code 不能为空"
    docname = "{}-{}".format(order_no, item_code)
    if not frappe.db.exists("BR SO BOM List", docname):
        return None, "未找到对应 BOM 清单: {}".format(docname)
    return docname, None


def _audit_apply_header(doc, header):
    float_fields = (
        ("running_cost_rate", ("running_cost_rate", "runningCostRate"), 0, 100),
        ("transport_fee_rate", ("transport_fee_rate", "transportFeeRate"), 0, 100),
        ("tax_rate", ("tax_rate", "taxRate"), 0, 1000),
        ("gross_margin", ("gross_margin", "grossMargin"), -1000, 1000),
    )
    for field, keys, lo, hi in float_fields:
        raw = _pick(header, *keys)
        _validate_percent(field, _to_float_or_none(raw), minimum=lo, maximum=hi)
        if raw not in (None, ""):
            setattr(doc, field, flt(raw))
    status = _pick(header, "status")
    if status not in (None, ""):
        doc.status = (status or "").strip()


def _apply_save_list_status_default(doc, header):
    """仅 save_so_bom_list：未在 header 显式传 status 时置为 saved，便于与 approved 区分。"""
    if not isinstance(header, dict):
        header = {}
    if "status" in header:
        raw = header.get("status")
        if raw is not None and str(raw).strip() != "":
            return
    doc.status = SO_BOM_LIST_STATUS_SAVED


def _audit_upsert_details(doc, jd):
    """按 name / 业务键更新已有行，否则 append；不传 details 视为 []；不删未传行。"""
    payload = jd.get("details")
    if payload is None:
        payload = []
    if not isinstance(payload, list):
        return "details 必须为数组"

    normalized = [_normalize_detail_row(r) for r in payload]
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
            target = existing_by_key.get(_detail_business_key(d))

        if target:
            for field, value in d.items():
                if field != "name" and value is not None:
                    setattr(target, field, value)
            continue

        if d.get("row_no") is None:
            max_row_no += 1
            d["row_no"] = max_row_no
        d["doctype"] = "BR SO BOM List Details"
        d.pop("name", None)
        doc.append("details", d)
    return None


def _audit_apply_approval(doc, jd, header):
    approved_by = _pick(header, "approved_by", "approvedBy")
    approved_on = _pick(header, "approved_on", "approvedOn")
    if cint(_pick(jd, "mark_approved", "markApproved"), 1):
        doc.status = SO_BOM_LIST_STATUS_APPROVED
        doc.approved_by = (approved_by or frappe.session.user or "").strip()
        doc.approved_on = approved_on or now_datetime()
        return
    if approved_by not in (None, ""):
        doc.approved_by = (approved_by or "").strip()
    if approved_on not in (None, ""):
        doc.approved_on = approved_on


def _so_bom_list_success_payload(doc, message, **extra_data):
    data = {
        "name": doc.name,
        "order_no": doc.order_no,
        "item_code": doc.item_code,
        "status": doc.status,
        "approved_by": doc.approved_by,
        "approved_on": doc.approved_on,
        "details_count": len(doc.details or []),
    }
    if extra_data:
        data.update(extra_data)
    return {
        "success": True,
        "message": message,
        "data": data,
    }


def _sales_order_distinct_item_codes(order_no):
    """销售订单下非空 item_code（去重顺序保留）。"""
    seen = set()
    out = []
    for ic in frappe.get_all(
        "Sales Order Item",
        filters={"parent": order_no, "parenttype": "Sales Order"},
        pluck="item_code",
    ):
        s = (ic or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _all_br_so_bom_list_rows_approved_for_sales_order(order_no):
    """
    若销售订单存在且至少有一行明细，则每个去重后的 item_code 均须存在
    BR SO BOM List（name = order_no-item_code）且 status 为 approved。
    """
    order_no = (order_no or "").strip()
    if not order_no or not frappe.db.exists("Sales Order", order_no):
        return False
    codes = _sales_order_distinct_item_codes(order_no)
    if not codes:
        return False
    for item_code in codes:
        name = "{}-{}".format(order_no, item_code)
        if not frappe.db.exists("BR SO BOM List", name):
            return False
        st = frappe.db.get_value("BR SO BOM List", name, "status")
        if (st or "").strip() != SO_BOM_LIST_STATUS_APPROVED:
            return False
    return True


def _try_submit_sales_order_after_full_bom_approval(order_no):
    """
    在 BOM 清单已 commit 之后调用：满足「全部 BOM 已 approved」且 SO 仍为草稿时执行 submit。
    提交失败不回滚已保存的 BR SO BOM List；返回仅供展示的字典并入 data。
    """
    order_no = (order_no or "").strip()
    if not order_no:
        return {
            "sales_order_submit": "skipped",
            "sales_order_message": "订单号为空，跳过销售订单提交",
        }
    if not frappe.db.exists("Sales Order", order_no):
        return {
            "sales_order_submit": "skipped",
            "sales_order_message": "未找到对应销售订单，跳过提交",
        }
    docstatus = frappe.db.get_value("Sales Order", order_no, "docstatus")
    if docstatus == 1:
        return {
            "sales_order_submit": "already_submitted",
            "sales_order_message": "销售订单已是已提交状态",
        }
    if cint(docstatus, 0) == 2:
        return {
            "sales_order_submit": "skipped",
            "sales_order_message": "销售订单已取消，跳过提交",
        }
    if not _all_br_so_bom_list_rows_approved_for_sales_order(order_no):
        return {
            "sales_order_submit": "skipped",
            "sales_order_message": "尚有成品未同步 BOM 清单或清单未全部审核通过，不提交销售订单",
        }
    try:
        so = frappe.get_doc("Sales Order", order_no)
        frappe.has_permission("Sales Order", doc=so, ptype="submit", throw=True)
        so.submit()
        frappe.db.commit()
        return {
            "sales_order_submit": "submitted",
            "sales_order_message": "销售订单已提交",
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="submit_sales_order_after_bom_audit",
            message=frappe.get_traceback(),
        )
        return {
            "sales_order_submit": "failed",
            "sales_order_message": str(e),
        }


def _persist_so_bom_list(
    jd,
    *,
    apply_approval,
    log_title,
    permission_denied_message,
    success_message,
):
    docname, early_msg = _audit_resolve_docname(jd)
    if early_msg:
        return {"success": False, "message": early_msg}

    raw_header = jd.get("header")
    header = raw_header if isinstance(raw_header, dict) else {}

    try:
        doc = frappe.get_doc("BR SO BOM List", docname)
        frappe.has_permission("BR SO BOM List", doc=doc, ptype="write", throw=True)
        _audit_apply_header(doc, header)
        if not apply_approval:
            _apply_save_list_status_default(doc, header)
        detail_msg = _audit_upsert_details(doc, jd)
        if detail_msg:
            return {"success": False, "message": detail_msg}
        if apply_approval:
            _audit_apply_approval(doc, jd, header)
        doc.save()
        frappe.db.commit()
        so_submit_extra = {}
        if apply_approval:
            so_submit_extra = _try_submit_sales_order_after_full_bom_approval(doc.order_no)
        return _so_bom_list_success_payload(doc, success_message, **so_submit_extra)
    except frappe.PermissionError:
        return {"success": False, "message": permission_denied_message}
    except Exception as e:
        frappe.log_error(title=log_title, message=frappe.get_traceback())
        return {"success": False, "message": str(e)}


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

    成功后若本订单下全部成品 BR SO BOM List 均为 approved，则可能自动 submit 销售订单；
    详见文档 §1a 与返回 data.sales_order_submit / sales_order_message。
    """
    jd = _parse_kwargs_json_data(kwargs)
    return _persist_so_bom_list(
        jd,
        apply_approval=True,
        log_title="audit_so_bom_list",
        permission_denied_message="无权限审核该 BOM 清单",
        success_message="审核保存成功",
    )


@frappe.whitelist(allow_guest=False)
def save_so_bom_list(**kwargs):
    """
    仅保存 BR SO BOM List 主表与子表明细（与审核接口相同的 header/details 入参），不做审核通过逻辑：
    不写默认审核人/时间；mark_approved 等均忽略。

    主表 status：未传 header.status 时自动置为 saved（模块常量 SO_BOM_LIST_STATUS_SAVED），
    与审核接口产生的 approved 区分；若在 header 中显式传 status 则使用传入值。
    """
    jd = _parse_kwargs_json_data(kwargs)
    return _persist_so_bom_list(
        jd,
        apply_approval=False,
        log_title="save_so_bom_list",
        permission_denied_message="无权限保存该 BOM 清单",
        success_message="保存成功",
    )


@frappe.whitelist(allow_guest=False)
def update_so_bom_list_status(**kwargs):
    """
    仅更新 BR SO BOM List 主表 status（审核/业务状态字段），不改明细、不改费率等。

    入参（json_data 或直接字段）:
        - sales_order_no / order_no + item_code：定位单据（与 save/audit 相同）
        - status / audit_status / auditStatus：新状态（必填，trim 后非空）
        - clear_approval_meta / clearApprovalMeta：可选，默认 1。
          为 1 且新 status 不是 approved 时，清空 approved_by、approved_on，避免「未审核却带审核人」。
          将 status 置为 approved 时不会自动填写审核人/时间（完整审核请用 audit_so_bom_list）。
    """
    jd = _parse_kwargs_json_data(kwargs)
    new_status = (_pick(jd, "status", "audit_status", "auditStatus") or "").strip()
    if not new_status:
        return {"success": False, "message": "status 不能为空"}

    docname, early_msg = _audit_resolve_docname(jd)
    if early_msg:
        return {"success": False, "message": early_msg}

    clear_meta = cint(_pick(jd, "clear_approval_meta", "clearApprovalMeta"), 1)

    try:
        doc = frappe.get_doc("BR SO BOM List", docname)
        frappe.has_permission("BR SO BOM List", doc=doc, ptype="write", throw=True)
        doc.status = new_status
        if clear_meta and new_status != SO_BOM_LIST_STATUS_APPROVED:
            doc.approved_by = None
            doc.approved_on = None
        doc.save()
        frappe.db.commit()
        return _so_bom_list_success_payload(doc, "状态已更新")
    except frappe.PermissionError:
        return {"success": False, "message": "无权限修改该 BOM 清单状态"}
    except Exception as e:
        frappe.log_error(title="update_so_bom_list_status", message=frappe.get_traceback())
        return {"success": False, "message": str(e)}

