# -*- coding: utf-8 -*-
"""在「包材」或「服务」下添加子物料组，可通过 bench execute 执行。"""

import frappe

PACKAGING_CHILDREN = [
    "纸箱",
    "泡沫垫板",
    "覆膜泡沫垫板",
    "泡沫坑盘",
    "覆膜泡沫坑盘",
    "泡沫沿边垫板",
    "覆膜泡沫沿边垫板",
    "PE膜",
    "无尘纸",
    "防尘袋",
    "吸塑",
    "透明胶带",
    "红色胶带",
    "缠绕膜",
]
PARENT_PACKAGING = "包材"

SERVICE_CHILDREN = [
    "弹簧加重块",
    "铁块加重块",
    "卷管加重块",
]
PARENT_SERVICE = "服务"


def add_packaging_item_groups():
    """在包材下创建上述子物料组（已存在则跳过）。"""
    if not frappe.db.exists("Item Group", PARENT_PACKAGING):
        frappe.throw(f"父物料组「{PARENT_PACKAGING}」不存在，请先创建。")
    created = []
    for name in PACKAGING_CHILDREN:
        if frappe.db.exists("Item Group", name):
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": name,
                "parent_item_group": PARENT_PACKAGING,
                "is_group": 0,
            }
        )
        doc.insert(ignore_permissions=True)
        created.append(name)
    frappe.db.commit()
    return {"created": created, "skipped": [n for n in PACKAGING_CHILDREN if n not in created]}


def add_service_item_groups():
    """在服务下创建弹簧加重块、铁块加重块、卷管加重块（已存在则改为挂到服务下）。"""
    if not frappe.db.exists("Item Group", PARENT_SERVICE):
        frappe.throw(f"父物料组「{PARENT_SERVICE}」不存在，请先创建。")
    created = []
    moved = []
    for name in SERVICE_CHILDREN:
        if frappe.db.exists("Item Group", name):
            doc = frappe.get_doc("Item Group", name)
            if doc.parent_item_group != PARENT_SERVICE:
                doc.parent_item_group = PARENT_SERVICE
                doc.save(ignore_permissions=True)
                moved.append(name)
        else:
            doc = frappe.get_doc(
                {
                    "doctype": "Item Group",
                    "item_group_name": name,
                    "parent_item_group": PARENT_SERVICE,
                    "is_group": 0,
                }
            )
            doc.insert(ignore_permissions=True)
            created.append(name)
    frappe.db.commit()
    return {"created": created, "moved": moved}
