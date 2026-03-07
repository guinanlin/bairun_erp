# Copyright (c) 2025, Bairun and contributors
# 采购未交列表接口单元测试。
#
# 运行:
#   bench --site <site> run-tests --module bairun_erp.utils.api.buying.test_purchase_order_unfulfilled_list

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.buying.purchase_order_list import get_purchase_order_unfulfilled_list


class TestPurchaseOrderUnfulfilledList(FrappeTestCase):
	"""测试 get_purchase_order_unfulfilled_list 采购未交列表接口"""

	def setUp(self):
		super(TestPurchaseOrderUnfulfilledList, self).setUp()
		# 清空 response，避免上次测试残留
		if hasattr(frappe.local, "response"):
			frappe.local.response.pop("message", None)
			frappe.local.response.pop("total_count", None)

	def tearDown(self):
		super(TestPurchaseOrderUnfulfilledList, self).tearDown()

	def test_response_structure(self):
		"""无参数调用：响应应包含 message（列表）和 total_count（整数）"""
		get_purchase_order_unfulfilled_list(
			json_data={"limit_start": 0, "limit_page_length": 20}
		)
		self.assertIn("message", frappe.response)
		self.assertIn("total_count", frappe.response)
		self.assertIsInstance(frappe.response["message"], list)
		self.assertIsInstance(frappe.response["total_count"], int)
		self.assertGreaterEqual(frappe.response["total_count"], 0)

	def test_empty_params(self):
		"""空 json_data：使用默认分页，不报错"""
		get_purchase_order_unfulfilled_list()
		self.assertIsInstance(frappe.response["message"], list)
		self.assertIsInstance(frappe.response["total_count"], int)

	def test_pagination(self):
		"""分页参数：limit_start、limit_page_length 生效"""
		get_purchase_order_unfulfilled_list(
			json_data={"limit_start": 0, "limit_page_length": 5}
		)
		msg = frappe.response["message"]
		total = frappe.response["total_count"]
		self.assertLessEqual(len(msg), 5)
		self.assertGreaterEqual(total, 0)
		# 总数应 >= 当前页条数
		if total > 0:
			self.assertGreaterEqual(total, len(msg))

	def test_search_params_no_error(self):
		"""传入搜索参数不报错，返回列表与总数"""
		get_purchase_order_unfulfilled_list(
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
		get_purchase_order_unfulfilled_list(
			json_data={"limit_start": 0, "limit_page_length": 1}
		)
		msg = frappe.response["message"]
		if not msg:
			self.skipTest("站点无未交行数据，跳过行结构校验")
		row = msg[0]
		required = [
			"purchase_order", "supplier", "supplier_name", "transaction_date",
			"schedule_date", "item_code", "item_name", "qty", "received_qty",
			"outstanding_qty", "rate", "amount", "outstanding_amount", "rowKey",
		]
		for key in required:
			self.assertIn(key, row, "未交行应包含字段: {}".format(key))
		self.assertGreater(row["outstanding_qty"], 0)
		self.assertEqual(row["outstanding_qty"], row["qty"] - row["received_qty"])
		self.assertIn("-", row["rowKey"])

	def test_order_by_param(self):
		"""传入 order_by 不报错"""
		get_purchase_order_unfulfilled_list(
			json_data={
				"order_by": "transaction_date desc, purchase_order asc, idx asc",
				"limit_start": 0,
				"limit_page_length": 5,
			}
		)
		self.assertIn("message", frappe.response)
		self.assertIn("total_count", frappe.response)
