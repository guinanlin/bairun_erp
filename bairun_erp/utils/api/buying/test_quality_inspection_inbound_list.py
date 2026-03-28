# Copyright (c) 2026, Bairun and contributors
#
# 运行:
#   bench --site <site> run-tests --module bairun_erp.utils.api.buying.test_quality_inspection_inbound_list

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.buying.purchase_order_list import get_purchase_order_unfulfilled_list
from bairun_erp.utils.api.buying.quality_inspection_inbound_list import (
	get_inbound_qc_line_detail,
	get_inbound_qc_list,
)


class TestQualityInspectionInboundList(FrappeTestCase):
	def setUp(self):
		super(TestQualityInspectionInboundList, self).setUp()
		if hasattr(frappe.local, "response"):
			frappe.local.response.pop("message", None)
			frappe.local.response.pop("total_count", None)

	def tearDown(self):
		super(TestQualityInspectionInboundList, self).tearDown()

	def test_list_response_structure(self):
		get_inbound_qc_list(json_data={"limit_start": 0, "limit_page_length": 20})
		self.assertIn("message", frappe.response)
		msg = frappe.response["message"]
		self.assertIsInstance(msg, dict)
		self.assertIn("items", msg)
		self.assertIn("total_count", msg)
		self.assertIsInstance(msg["items"], list)
		self.assertIsInstance(msg["total_count"], int)
		self.assertGreaterEqual(msg["total_count"], 0)

	def test_list_pagination_and_max_limit(self):
		get_inbound_qc_list(json_data={"limit_start": 0, "limit_page_length": 500})
		msg = frappe.response["message"]
		self.assertLessEqual(len(msg["items"]), 100)

	def test_list_filters_no_error(self):
		get_inbound_qc_list(
			json_data={
				"qc_line_status": "pending",
				"search_purchase_receipt": "MAT",
				"search_supplier": "供",
				"search_item": "ITEM",
				"search_purchase_order": "PUR",
				"search_sales_order": "SO",
				"from_posting_date": "2020-01-01",
				"to_posting_date": "2099-12-31",
				"order_by": "posting_date desc, purchase_receipt desc, idx asc",
			}
		)
		self.assertIn("items", frappe.response["message"])

	def test_list_row_keys_when_has_data(self):
		get_inbound_qc_list(json_data={"limit_start": 0, "limit_page_length": 1})
		items = frappe.response["message"]["items"]
		if not items:
			self.skipTest("无已提交 PR 行，跳过")
		row = items[0]
		for k in (
			"purchase_receipt",
			"pr_item_name",
			"posting_date",
			"qty",
			"qc_line_status",
			"quality_inspection_count",
			"row_key",
		):
			self.assertIn(k, row)
		self.assertIn(row["qc_line_status"], ("pending", "done"))

	def test_pending_qc_consistency_with_unfulfilled(self):
		"""采购未交 pending_qc 指向的 PR 行，在入库质检 pending 筛选下应可查见。"""
		get_purchase_order_unfulfilled_list(json_data={"limit_start": 0, "limit_page_length": 200})
		unf = frappe.response.get("message") or []
		pending_pairs = []
		for r in unf:
			pq = r.get("pending_qc")
			if pq and pq.get("purchase_receipt") and pq.get("pr_item_name"):
				pending_pairs.append((pq["purchase_receipt"], pq["pr_item_name"]))
		if not pending_pairs:
			self.skipTest("当前无 pending_qc 数据，跳过一致性校验")

		pr_name, pri_name = pending_pairs[0]
		get_inbound_qc_list(
			json_data={
				"qc_line_status": "pending",
				"search_purchase_receipt": pr_name,
				"limit_page_length": 100,
			}
		)
		items = frappe.response["message"]["items"]
		found = any(
			i.get("purchase_receipt") == pr_name and i.get("pr_item_name") == pri_name for i in items
		)
		self.assertTrue(
			found,
			"未交列表 pending_qc 应对应入库质检 pending 列表中的同一 PR 行",
		)

	def test_detail_requires_params(self):
		with self.assertRaises(Exception):
			get_inbound_qc_line_detail(json_data={})
