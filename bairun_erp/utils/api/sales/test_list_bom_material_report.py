# Copyright (c) 2026, Bairun and contributors
# list_bom_material_report 白名单接口单元测试。
#
# 运行:
#   bench --site <site> run-tests --module bairun_erp.utils.api.sales.test_list_bom_material_report

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.sales.sales_order_query_bom_details import (
    list_bom_material_report,
    _display_bom_status,
    _row_to_bom_report_item,
)


class TestListBomMaterialReport(FrappeTestCase):
    """测试 list_bom_material_report BOM 物料清单报表列表"""

    def test_missing_dates(self):
        """缺少日期：失败并提示"""
        r = list_bom_material_report(json_data={})
        self.assertFalse(r.get("success"))
        self.assertIn("message", r)
        self.assertIn("date_from", r["message"])

    def test_invalid_date(self):
        """非法日期格式：失败"""
        r = list_bom_material_report(
            json_data={"date_from": "not-a-date", "date_to": "2026-12-31"}
        )
        self.assertFalse(r.get("success"))
        self.assertIn("message", r)

    def test_date_from_after_to(self):
        """起止颠倒：失败"""
        r = list_bom_material_report(
            json_data={"date_from": "2026-12-31", "date_to": "2025-01-01"}
        )
        self.assertFalse(r.get("success"))

    def test_success_structure(self):
        """成功时包含 data 分页字段与 items 列表"""
        r = list_bom_material_report(
            json_data={
                "date_from": "2000-01-01",
                "date_to": "2099-12-31",
                "page_number": 1,
                "page_size": 10,
            }
        )
        self.assertTrue(r.get("success"), msg=r.get("message"))
        data = r.get("data") or {}
        for key in ("page_number", "page_size", "total_count", "total_pages", "items"):
            self.assertIn(key, data, "data 应包含 {}".format(key))
        self.assertIsInstance(data["items"], list)
        self.assertLessEqual(len(data["items"]), 10)
        self.assertEqual(data["page_number"], 1)
        self.assertEqual(data["page_size"], 10)

    def test_item_row_keys_when_has_data(self):
        """若有数据，items 元素含前端约定字段"""
        r = list_bom_material_report(
            json_data={
                "date_from": "2000-01-01",
                "date_to": "2099-12-31",
                "page_size": 1,
            }
        )
        self.assertTrue(r.get("success"), msg=r.get("message"))
        items = (r.get("data") or {}).get("items") or []
        if not items:
            self.skipTest("站点无 BR SO BOM List 数据，跳过行字段校验")
        row = items[0]
        required = [
            "id",
            "bomStatus",
            "salesOrderNo",
            "unitCode",
            "unitName",
            "itemCode",
            "itemName",
            "deliveryDate",
            "materialAuditor",
            "materialAuditDate",
            "documentCreator",
        ]
        for k in required:
            self.assertIn(k, row)

    def test_page_size_cap(self):
        """page_size 超过上限时压到 100"""
        r = list_bom_material_report(
            json_data={
                "date_from": "2000-01-01",
                "date_to": "2099-12-31",
                "page_size": 500,
            }
        )
        self.assertTrue(r.get("success"), msg=r.get("message"))
        self.assertEqual(r["data"]["page_size"], 100)

    def test_display_bom_status(self):
        self.assertEqual(_display_bom_status("draft"), "未审核")
        self.assertEqual(_display_bom_status("已审核"), "已审核")
        self.assertEqual(_display_bom_status("approved"), "已审核")

    def test_row_to_bom_report_item(self):
        row = {
            "name": "SO1-ITEM1",
            "order_no": "SO1",
            "status": "draft",
            "customer_code": "C1",
            "customer_name": "客户",
            "item_code": "ITEM1",
            "item_name": "成品",
            "delivery_date": frappe.utils.getdate("2026-03-15"),
            "approved_by": "审核人",
            "approved_on": None,
            "created_by": "制单人",
        }
        item = _row_to_bom_report_item(row, 0)
        self.assertEqual(item["id"], "SO1-ITEM1")
        self.assertEqual(item["bomStatus"], "未审核")
        self.assertEqual(item["salesOrderNo"], "SO1")
        self.assertEqual(item["deliveryDate"], "2026-03-15")
