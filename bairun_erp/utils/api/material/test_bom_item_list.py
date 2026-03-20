from __future__ import unicode_literals

from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.material.bom_item_list import (
    _detail_business_key,
    _normalize_detail_row,
    audit_so_bom_list,
)


class TestBomItemListAudit(FrappeTestCase):
    def test_normalize_detail_row_supports_camel_case(self):
        row = _normalize_detail_row(
            {
                "rowNo": 3,
                "itemCode": "RM-001",
                "level": 2,
                "bomCode": "A1-1",
                "itemName": "原材料A",
                "supplierName": "供应商A",
                "process": "热处理",
            }
        )
        self.assertEqual(row["row_no"], 3)
        self.assertEqual(row["item_code"], "RM-001")
        self.assertEqual(row["bom_code"], "A1-1")
        self.assertEqual(row["process_name"], "热处理")

    def test_business_key_uses_row_item_bom_level(self):
        key = _detail_business_key(
            {
                "row_no": 2,
                "item_code": "RM-002",
                "bom_code": "A1-2",
                "level": 3,
            }
        )
        self.assertEqual(key, (2, "RM-002", "A1-2", 3))

    def test_audit_requires_order_no(self):
        ret = audit_so_bom_list(json_data={"item_code": "FG-001"})
        self.assertFalse(ret.get("success"))
        self.assertIn("sales_order_no", ret.get("message", ""))

    def test_audit_requires_item_code(self):
        ret = audit_so_bom_list(json_data={"sales_order_no": "SO-TEST-001"})
        self.assertFalse(ret.get("success"))
        self.assertIn("item_code", ret.get("message", ""))
