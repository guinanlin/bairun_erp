from __future__ import unicode_literals

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.buying.purchase_order_add import (
    _br_so_bom_list_all_details_have_purchase_order,
)
from bairun_erp.utils.api.material.bom_item_list import (
    SO_BOM_LIST_STATUS_APPROVED,
    SO_BOM_LIST_STATUS_SAVED,
    _all_br_so_bom_list_rows_approved_for_sales_order,
    _apply_save_list_status_default,
    _detail_business_key,
    _normalize_detail_row,
    audit_so_bom_list,
    save_so_bom_list,
    update_so_bom_list_status,
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

    def test_save_requires_order_no(self):
        ret = save_so_bom_list(json_data={"item_code": "FG-001"})
        self.assertFalse(ret.get("success"))
        self.assertIn("sales_order_no", ret.get("message", ""))

    def test_save_default_status_sets_saved(self):
        class Doc(object):
            status = "approved"

        doc = Doc()
        _apply_save_list_status_default(doc, {})
        self.assertEqual(doc.status, SO_BOM_LIST_STATUS_SAVED)

    def test_save_respects_explicit_header_status(self):
        class Doc(object):
            status = "x"

        doc = Doc()
        _apply_save_list_status_default(doc, {"status": "pending_review"})
        self.assertEqual(doc.status, "x")

    def test_update_status_requires_status_value(self):
        ret = update_so_bom_list_status(
            json_data={"sales_order_no": "SO-X", "item_code": "FG-001"},
        )
        self.assertFalse(ret.get("success"))
        self.assertIn("status", ret.get("message", ""))

    def test_all_br_so_bom_approved_false_without_sales_order(self):
        with patch("frappe.db.exists", return_value=False):
            self.assertFalse(
                _all_br_so_bom_list_rows_approved_for_sales_order("SO-NONE"),
            )

    def test_all_br_so_bom_approved_false_when_so_has_no_line_items(self):
        with patch("frappe.db.exists", return_value=True), patch(
            "frappe.get_all",
            return_value=[],
        ):
            self.assertFalse(
                _all_br_so_bom_list_rows_approved_for_sales_order("SO-1"),
            )

    def test_all_br_so_bom_approved_true_when_each_list_approved(self):
        with patch("frappe.db.exists", return_value=True), patch(
            "frappe.get_all",
            return_value=["IC1", "IC2"],
        ), patch(
            "frappe.db.get_value",
            return_value=SO_BOM_LIST_STATUS_APPROVED,
        ):
            self.assertTrue(
                _all_br_so_bom_list_rows_approved_for_sales_order("SO-1"),
            )

    def test_all_br_so_bom_approved_false_when_one_not_approved(self):
        with patch("frappe.db.exists", return_value=True), patch(
            "frappe.get_all",
            return_value=["IC1", "IC2"],
        ), patch(
            "frappe.db.get_value",
            side_effect=[SO_BOM_LIST_STATUS_APPROVED, "saved"],
        ):
            self.assertFalse(
                _all_br_so_bom_list_rows_approved_for_sales_order("SO-1"),
            )

    def test_br_so_bom_all_details_have_po_requires_nonempty_po(self):
        with patch("frappe.db.exists", return_value=True), patch(
            "frappe.db.count",
            return_value=2,
        ), patch(
            "frappe.db.sql",
            return_value=[
                {"purchase_order_no": "PO-1"},
                {"purchase_order_no": ""},
            ],
        ):
            self.assertFalse(
                _br_so_bom_list_all_details_have_purchase_order("SO-1-FG"),
            )

    def test_br_so_bom_all_details_have_po_true(self):
        with patch("frappe.db.exists", return_value=True), patch(
            "frappe.db.count",
            return_value=2,
        ), patch(
            "frappe.db.sql",
            return_value=[
                {"purchase_order_no": "PO-1"},
                {"purchase_order_no": "PO-2"},
            ],
        ):
            self.assertTrue(
                _br_so_bom_list_all_details_have_purchase_order("SO-1-FG"),
            )
