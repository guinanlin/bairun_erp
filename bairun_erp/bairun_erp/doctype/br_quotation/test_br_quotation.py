# Copyright (c) 2025, guinan.lin@foxmail.com and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from bairun_erp.bairun_erp.doctype.br_quotation.br_quotation import get_quotation_list


class TestBRQuotation(FrappeTestCase):
	def setUp(self):
		"""设置测试环境"""
		# 创建测试数据
		self.create_test_data()
	
	def create_test_data(self):
		"""创建测试数据"""
		# 创建客户报价单
		customer_quotation = frappe.get_doc({
			"doctype": "BR Customer Quotation",
			"quotation_number": "TEST-001",
			"customer_name": "测试客户",
			"product_name": "测试产品"
		})
		customer_quotation.insert(ignore_permissions=True)
		
		# 创建报价单版本
		quotation_v1 = frappe.get_doc({
			"doctype": "BR Quotation",
			"quotation_number": "TEST-001",
			"customer_name": "测试客户",
			"product_name": "测试产品",
			"version_id": "V1",
			"version_name": "版本1"
		})
		quotation_v1.insert(ignore_permissions=True)
		
		quotation_v2 = frappe.get_doc({
			"doctype": "BR Quotation",
			"quotation_number": "TEST-001",
			"customer_name": "测试客户",
			"product_name": "测试产品",
			"version_id": "V2",
			"version_name": "版本2"
		})
		quotation_v2.insert(ignore_permissions=True)
		
		# 创建另一个报价单
		customer_quotation2 = frappe.get_doc({
			"doctype": "BR Customer Quotation",
			"quotation_number": "TEST-002",
			"customer_name": "测试客户2",
			"product_name": "测试产品2"
		})
		customer_quotation2.insert(ignore_permissions=True)
		
		quotation_v3 = frappe.get_doc({
			"doctype": "BR Quotation",
			"quotation_number": "TEST-002",
			"customer_name": "测试客户2",
			"product_name": "测试产品2",
			"version_id": "V3",
			"version_name": "版本3"
		})
		quotation_v3.insert(ignore_permissions=True)
	
	def test_get_quotation_list_with_version_filter(self):
		"""测试按版本号过滤报价单列表"""
		# 测试按版本号V1过滤
		result = get_quotation_list(
			page=1,
			page_size=10,
			filters={"version_id": "V1"}
		)
		
		self.assertEqual(result['status'], 'success')
		self.assertGreater(len(result['data']['customer_quotations']), 0)
		
		# 验证返回的报价单包含V1版本
		found_v1 = False
		for quotation in result['data']['customer_quotations']:
			if quotation['quotation_number'] == 'TEST-001':
				for version in quotation['versions']:
					if version['version_id'] == 'V1':
						found_v1 = True
						break
				break
		
		self.assertTrue(found_v1, "应该找到包含V1版本的报价单")
	
	def test_get_quotation_list_with_version_sort(self):
		"""测试按版本号排序报价单列表"""
		# 测试按版本号降序排序
		result = get_quotation_list(
			page=1,
			page_size=10,
			filters={},
			order_by="version_id",
			order_direction="desc"
		)
		
		self.assertEqual(result['status'], 'success')
		self.assertGreater(len(result['data']['customer_quotations']), 0)
		
		# 验证排序结果（这里只是验证函数能正常执行）
		self.assertIn('customer_quotations', result['data'])
	
	def tearDown(self):
		"""清理测试数据"""
		# 删除测试数据
		frappe.db.sql("DELETE FROM `tabBR Quotation` WHERE quotation_number IN ('TEST-001', 'TEST-002')")
		frappe.db.sql("DELETE FROM `tabBR Customer Quotation` WHERE quotation_number IN ('TEST-001', 'TEST-002')")
		frappe.db.commit()
