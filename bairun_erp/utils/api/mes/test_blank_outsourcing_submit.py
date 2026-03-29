# Copyright (c) 2026, Bairun and contributors
# 委外编排白名单接口单元测试。
#
# 运行:
#   bench --site site2.local run-tests --module bairun_erp.utils.api.mes.test_blank_outsourcing_submit

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.mes.blank_outsourcing_submit import (
	CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT,
	LOG_DOCTYPE,
	PO_DEFAULT_SERVICE_ITEM_NAME_SEMI,
	_apply_semi_finished_outsourcing_defaults,
	submit_blank_outsourcing,
	submit_semi_finished_outsourcing,
)


class TestBlankOutsourcingSubmit(FrappeTestCase):
	def test_validation_empty_items(self):
		out = submit_blank_outsourcing(json_data={})
		self.assertFalse(out.get("success"))
		self.assertEqual(out.get("error_code"), "VALIDATION_ERROR")

	def test_validation_missing_po_items_when_not_skip(self):
		out = submit_blank_outsourcing(
			json_data={
				"company": "BR",
				"from_warehouse": "毛坯 - B",
				"supplier": "SUP-001",
				"sales_order": "SAL-ORD-2026-00018",
				"items": [{"item_code": "_nonexistent_item_xyz_", "qty": 1}],
			}
		)
		self.assertFalse(out.get("success"))
		self.assertEqual(out.get("error_code"), "VALIDATION_ERROR")
		self.assertIn("po_items", out.get("message", ""))

	def test_validation_sales_order_required(self):
		out = submit_blank_outsourcing(
			json_data={
				"company": "BR",
				"from_warehouse": "毛坯 - B",
				"items": [{"item_code": "ANY", "qty": 1}],
				"skip_purchase_order": True,
			}
		)
		self.assertFalse(out.get("success"))
		self.assertEqual(out.get("error_code"), "VALIDATION_ERROR")
		self.assertIn("sales_order", out.get("message", ""))

	def test_idempotency_replay_success(self):
		key = "test-idem-{}".format(frappe.generate_hash(length=8))
		# 预置成功日志
		log = frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"idempotency_key": key,
				"status": "Success",
				"material_request_name": "MAT-MR-TEST-REPLAY",
				"stock_entry_name": "MAT-STE-TEST-REPLAY",
				"purchase_order_name": "PO-TEST-REPLAY",
				"purchase_order_semi_finished_name": "PO-TEST-REPLAY-SEMI",
			}
		)
		log.insert(ignore_permissions=True)
		frappe.db.commit()

		out = submit_blank_outsourcing(json_data={"idempotency_key": key, "items": []})
		self.assertTrue(out.get("success"))
		self.assertTrue(out.get("replayed"))
		self.assertEqual(out.get("material_request_name"), "MAT-MR-TEST-REPLAY")
		self.assertEqual(out.get("stock_entry_name"), "MAT-STE-TEST-REPLAY")
		self.assertIsNone(out.get("receipt_stock_entry_name"))
		self.assertEqual(out.get("purchase_order_name"), "PO-TEST-REPLAY")
		self.assertEqual(out.get("purchase_order_semi_finished_name"), "PO-TEST-REPLAY-SEMI")

		frappe.delete_doc(LOG_DOCTYPE, log.name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_semi_finished_validation_empty_items(self):
		out = submit_semi_finished_outsourcing(json_data={})
		self.assertFalse(out.get("success"))
		self.assertEqual(out.get("error_code"), "VALIDATION_ERROR")

	def test_semi_finished_validation_sales_order_required_when_skip_po(self):
		out = submit_semi_finished_outsourcing(
			json_data={
				"company": "BR",
				"from_warehouse": "半成品 - B",
				"items": [{"item_code": "ANY", "qty": 1}],
				"skip_purchase_order": True,
			}
		)
		self.assertFalse(out.get("success"))
		self.assertEqual(out.get("error_code"), "VALIDATION_ERROR")
		self.assertIn("sales_order", out.get("message", ""))

	def test_apply_semi_finished_defaults(self):
		p = {}
		_apply_semi_finished_outsourcing_defaults(p)
		self.assertEqual(p.get("custom_business_type"), CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT)
		self.assertEqual(p.get("po_service_item_name"), PO_DEFAULT_SERVICE_ITEM_NAME_SEMI)

	def test_apply_semi_finished_defaults_respects_explicit_po_service(self):
		p = {"po_service_item_name": "自定义委外服务"}
		_apply_semi_finished_outsourcing_defaults(p)
		self.assertEqual(p.get("custom_business_type"), CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT)
		self.assertEqual(p.get("po_service_item_name"), "自定义委外服务")

	def test_apply_semi_finished_defaults_skip_po_no_po_service(self):
		p = {"skip_purchase_order": True}
		_apply_semi_finished_outsourcing_defaults(p)
		self.assertEqual(p.get("custom_business_type"), CUSTOM_BUSINESS_TYPE_SEMI_DEFAULT)
		self.assertNotIn("po_service_item_name", p)

	def test_semi_finished_idempotency_replay_success(self):
		key = "test-idem-semi-{}".format(frappe.generate_hash(length=8))
		log = frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"idempotency_key": key,
				"status": "Success",
				"material_request_name": "MAT-MR-TEST-SEMI",
				"stock_entry_name": "MAT-STE-TEST-SEMI",
				"purchase_order_name": "PO-TEST-SEMI",
			}
		)
		log.insert(ignore_permissions=True)
		frappe.db.commit()

		out = submit_semi_finished_outsourcing(json_data={"idempotency_key": key, "items": []})
		self.assertTrue(out.get("success"))
		self.assertTrue(out.get("replayed"))
		self.assertEqual(out.get("material_request_name"), "MAT-MR-TEST-SEMI")

		frappe.delete_doc(LOG_DOCTYPE, log.name, force=True, ignore_permissions=True)
		frappe.db.commit()
