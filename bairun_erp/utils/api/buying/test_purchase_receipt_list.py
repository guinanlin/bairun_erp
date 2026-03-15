# Copyright (c) 2025, Bairun and contributors
# 采购明细列表接口单元测试。
#
# 运行:
#   bench --site <site> run-tests --module bairun_erp.utils.api.buying.test_purchase_receipt_list

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.buying.purchase_receipt_list import get_purchase_receipt_details_list


class TestPurchaseReceiptDetailsList(FrappeTestCase):
	"""测试 get_purchase_receipt_details_list 采购明细列表接口"""

	def setUp(self):
		super(TestPurchaseReceiptDetailsList, self).setUp()
		if hasattr(frappe.local, "response"):
			frappe.local.response.pop("message", None)
			frappe.local.response.pop("total_count", None)

	def tearDown(self):
		super(TestPurchaseReceiptDetailsList, self).tearDown()

	def test_response_structure(self):
		"""无参数调用：响应应包含 message（列表）和 total_count（整数）"""
		get_purchase_receipt_details_list(
			json_data={"limit_start": 0, "limit_page_length": 20}
		)
		self.assertIn("message", frappe.response)
		self.assertIn("total_count", frappe.response)
		self.assertIsInstance(frappe.response["message"], list)
		self.assertIsInstance(frappe.response["total_count"], int)
		self.assertGreaterEqual(frappe.response["total_count"], 0)

	def test_empty_params(self):
		"""空 json_data：使用默认分页，不报错"""
		get_purchase_receipt_details_list()
		self.assertIsInstance(frappe.response["message"], list)
		self.assertIsInstance(frappe.response["total_count"], int)

	def test_pagination(self):
		"""分页参数：limit_start、limit_page_length 生效"""
		get_purchase_receipt_details_list(
			json_data={"limit_start": 0, "limit_page_length": 5}
		)
		msg = frappe.response["message"]
		total = frappe.response["total_count"]
		self.assertLessEqual(len(msg), 5)
		self.assertGreaterEqual(total, 0)
		if total > 0:
			self.assertGreaterEqual(total, len(msg))

	def test_search_params_no_error(self):
		"""传入搜索参数不报错，返回列表与总数"""
		get_purchase_receipt_details_list(
			json_data={
				"limit_start": 0,
				"limit_page_length": 10,
				"search_customer_order": "SO-",
				"search_supplier": "供",
				"search_item_name": "物料",
			}
		)
		self.assertIsInstance(frappe.response["message"], list)
		self.assertIsInstance(frappe.response["total_count"], int)

	def test_row_structure_when_has_data(self):
		"""若有数据，每行应包含约定字段"""
		get_purchase_receipt_details_list(
			json_data={"limit_start": 0, "limit_page_length": 1}
		)
		msg = frappe.response["message"]
		if not msg:
			self.skipTest("站点无采购明细数据，跳过行结构校验")
		row = msg[0]
		required = [
			"receipt_name", "purchase_order", "customer_order", "supplier", "supplier_name",
			"posting_date", "item_code", "item_name", "order_qty", "received_qty",
			"outstanding_qty", "rate", "amount", "billed_amt", "invoiced_percent",
			"warehouse", "warehouse_slot", "rowKey",
		]
		for key in required:
			self.assertIn(key, row, "明细行应包含字段: {}".format(key))
		self.assertGreater(row["received_qty"], 0)
		self.assertEqual(
			row["outstanding_qty"],
			max(0, row["order_qty"] - row["received_qty"]),
		)
		self.assertIn("-", row["rowKey"])

	def test_order_by_param(self):
		"""传入 order_by 不报错"""
		get_purchase_receipt_details_list(
			json_data={
				"order_by": "posting_date desc, receipt_name asc, idx asc",
				"limit_start": 0,
				"limit_page_length": 5,
			}
		)
		self.assertIn("message", frappe.response)
		self.assertIn("total_count", frappe.response)
