# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
标准 ERPNext/Frappe BOM 删除（白名单）。

对已提交（docstatus == 1）的 BOM 会先执行与界面一致的 Cancel，再 delete_doc；
草稿直接删除；已取消（docstatus == 2）跳过 Cancel、仅删除。
从而走完整校验（权限、链接检查、on_cancel / on_trash / after_delete、
Deleted Document、附件清理等）。

调用示例:
    POST /api/method/bairun_erp.utils.api.material.bom_delete.delete_bom
    { "json_data": { "bom_name": "BOM-00001" } }
    或 { "json_data": { "items": ["BOM-00001", "BOM-00002"] } }
"""

from __future__ import unicode_literals

import json

import frappe
from frappe import _


_DOCTYPE = "BOM"


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


def _normalize_bom_names(jd):
    """解析单条或多条 BOM name，去重保序。"""
    single = _pick(jd, "name", "bom_name", "bomName")
    raw_list = _pick(jd, "names", "bom_names", "bomNames", "items")

    names = []
    if single is not None and str(single).strip():
        names.append(str(single).strip())

    if raw_list is not None:
        if isinstance(raw_list, str):
            try:
                raw_list = json.loads(raw_list)
            except (TypeError, ValueError):
                frappe.throw(_("参数 items/names 必须是数组或 JSON 数组字符串"))

        if not isinstance(raw_list, list):
            frappe.throw(_("参数 items/names 必须是数组"))

        for x in raw_list:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                names.append(s)

    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)

    if not out:
        frappe.throw(_("请提供 BOM 编号：name / bom_name 或 items / names 数组"))

    return out


def _delete_one_bom(name):
    """
    删除单张 BOM：已提交则先 cancel 再 delete_doc（与 ERPNext 单据生命周期一致），
    最后 commit；调用方在异常时 rollback。
    """
    if not frappe.db.exists(_DOCTYPE, name):
        frappe.throw(_("找不到 BOM：{0}").format(name), frappe.DoesNotExistError)

    doc = frappe.get_doc(_DOCTYPE, name)
    if doc.docstatus == 1:
        doc.cancel()
    elif doc.docstatus == 2:
        pass
    elif doc.docstatus != 0:
        frappe.throw(
            _("BOM {0} 状态异常（docstatus={1}），无法删除").format(name, doc.docstatus)
        )

    frappe.delete_doc(_DOCTYPE, name, ignore_missing=False)
    frappe.db.commit()


@frappe.whitelist(allow_guest=False)
def delete_bom(**kwargs):
    """
    按 ERPNext 标准规则删除一张或多张 BOM。

    参数（可直接传或放在 json_data 内）:
        - name / bom_name / bomName: 单条 BOM 的 name
        - names / bom_names / bomNames / items: 多条 name 列表（可与单条同时传，会合并去重）

    返回:
        {
            "success": bool,  # 仅当全部成功时为 True
            "message": str,
            "data": {
                "deleted": [...],
                "failed": [{"name": str, "message": str}, ...]
            }
        }
    """
    jd = _parse_kwargs_json_data(kwargs)
    names = _normalize_bom_names(jd)

    deleted = []
    failed = []

    for name in names:
        try:
            _delete_one_bom(name)
            deleted.append(name)
        except Exception as e:
            frappe.db.rollback()
            failed.append({"name": name, "message": str(e)})

    all_ok = len(failed) == 0
    if all_ok:
        msg = _("已删除 {0} 张 BOM").format(len(deleted)) if deleted else _("未执行删除")
    elif deleted:
        msg = _("部分成功：已删除 {0} 张，失败 {1} 张").format(len(deleted), len(failed))
    else:
        msg = _("删除失败")

    return {
        "success": all_ok,
        "message": msg,
        "data": {
            "deleted": deleted,
            "failed": failed,
        },
    }
