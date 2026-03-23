# Copyright (c) 2025, Bairun and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase

from bairun_erp.utils.api.material.item import add_packaging_material, add_item_target_customer, get_raw_material_item


# 测试用的物料编码，若库里没有可改为已有物料或跳过测试
TEST_ITEM_CODE = "test005"


class TestRawMaterialItem(FrappeTestCase):
	"""测试 get_raw_material_item 白名单方法"""

	def test_item_not_found_returns_error(self):
		res = get_raw_material_item(item_code="__nonexistent_item_xyz__")
		self.assertIn("error", res)
		self.assertIn("does not exist", res["error"])

	def test_no_item_code_returns_paginated_list(self):
		"""不传 item_code 时返回分页列表 data/total/page/page_size"""
		res = get_raw_material_item()
		self.assertIn("data", res)
		self.assertIn("total", res)
		self.assertIn("page", res)
		self.assertIn("page_size", res)
		self.assertIsInstance(res["data"], list)
		self.assertGreaterEqual(res["total"], 0)
		self.assertEqual(res["page"], 1)
		self.assertEqual(res["page_size"], 20)

	def test_get_raw_material_item_structure_and_item_fields(self):
		"""存在物料时，返回结构正确且 id/date/itemFullName/unit 等来自 Item 表"""
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		res = get_raw_material_item(item_code=TEST_ITEM_CODE)
		self.assertNotIn("error", res, msg=f"get_raw_material_item 返回了错误: {res.get('error')}")

		# 必有的主列表字段
		for key in (
			"id", "date", "projectNo", "itemFullName", "orderQty", "unitPrice",
			"receivedQty", "unreceivedQty", "inStockQty", "supplierId", "supplier",
			"inventoryCost", "salesPrice", "warehouse", "warehouseLocation",
			"unit", "workInstructionUrl", "status",
		):
			self.assertIn(key, res, msg=f"缺少字段: {key}")

		# 来自 Item 的字段应与数据库一致
		item = frappe.get_cached_doc("Item", TEST_ITEM_CODE)
		self.assertEqual(res["id"], TEST_ITEM_CODE)
		self.assertEqual(res["itemFullName"], item.item_name or TEST_ITEM_CODE)
		self.assertEqual(res["unit"], item.stock_uom or "")
		self.assertEqual(res["inventoryCost"], float(item.valuation_rate or 0))
		self.assertEqual(res["salesPrice"], float(item.standard_rate or 0))
		# 采购相关放空/0
		self.assertEqual(res["projectNo"], "")
		self.assertEqual(res["orderQty"], 0)
		self.assertEqual(res["unitPrice"], 0)
		self.assertEqual(res["receivedQty"], 0)
		self.assertEqual(res["unreceivedQty"], 0)
		self.assertEqual(res["supplierId"], "")
		self.assertEqual(res["supplier"], "")
		self.assertEqual(res["warehouseLocation"], "")
		self.assertEqual(res["workInstructionUrl"], "")
		self.assertEqual(res["status"], "")

		# date 应为 creation 的 yyyy-MM-dd
		from frappe.utils import getdate
		expected_date = getdate(item.creation).strftime("%Y-%m-%d") if item.creation else ""
		self.assertEqual(res["date"], expected_date)

		# inStockQty 应为 Bin 汇总（数值类型）
		self.assertIsInstance(res["inStockQty"], (int, float))


class TestItemTargetCustomers(FrappeTestCase):
	"""测试 add_item_target_customer 白名单方法"""

	def test_add_item_target_customer_appends_to_item_table(self):
		# 若自定义字段尚未安装（未 migrate），跳过
		if not frappe.get_meta("Item").get_field("br_target_customers"):
			self.skipTest('Custom field "br_target_customers" not found on Item (need migrate)')

		# 找一个可用的 Item Group / Customer Group / Territory
		item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
		if not item_group or not customer_group or not territory:
			self.skipTest("Missing masters: Item Group / Customer Group / Territory")

		customer_name = "__BR_TEST_CUSTOMER__"
		if not frappe.db.exists("Customer", customer_name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": customer_name,
					"customer_group": customer_group,
					"territory": territory,
				}
			).insert(ignore_permissions=True)

		item_code = "__BR_TEST_FG_ITEM__"
		if not frappe.db.exists("Item", item_code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": item_code,
					"item_name": item_code,
					"item_group": item_group,
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"is_sales_item": 1,
				}
			).insert(ignore_permissions=True)

		res = add_item_target_customer(item_code=item_code, customer=customer_name)
		self.assertEqual(res["item_code"], item_code)
		self.assertEqual(res["customer"], customer_name)
		self.assertIn(customer_name, res["target_customers"])

		# 再调用一次应该不重复添加
		res2 = add_item_target_customer(item_code=item_code, customer=customer_name)
		self.assertIn(customer_name, res2["target_customers"])
		self.assertEqual(res2["target_customers"].count(customer_name), 1)

	def test_add_item_target_customer_creates_missing_customer_and_item(self):
		# 若自定义字段尚未安装（未 migrate），跳过
		if not frappe.get_meta("Item").get_field("br_target_customers"):
			self.skipTest('Custom field "br_target_customers" not found on Item (need migrate)')

		missing_customer = "__BR_TEST_AUTO_CUSTOMER__"
		missing_item = "__BR_TEST_AUTO_ITEM__"

		# 先确保不存在
		if frappe.db.exists("Customer", missing_customer) or frappe.db.exists("Item", missing_item):
			self.skipTest("Auto-create test docs already exist; please delete and re-run")

		res = add_item_target_customer(item_code=missing_item, customer=missing_customer)
		self.assertTrue(frappe.db.exists("Customer", missing_customer))
		self.assertTrue(frappe.db.exists("Item", missing_item))
		self.assertEqual(res["item_code"], missing_item)
		self.assertEqual(res["customer"], missing_customer)
		self.assertIn(missing_customer, res["target_customers"])


class TestAddPackagingMaterial(FrappeTestCase):
	"""测试 add_packaging_material 白名单方法：至少能成功添加一个包材（纸箱）"""

	def setUp(self):
		# 测试用唯一规格，避免与已有数据冲突
		self.test_length = "0.301"
		self.test_width = "0.302"
		self.test_height = "0.303"
		self.created_item_code = None

	def tearDown(self):
		# 清理：删除本用例创建的 Item（站点若为 Naming Series，item_code 为流水号而非传入值）
		code = self.created_item_code
		if code and frappe.db.exists("Item", code):
			frappe.delete_doc("Item", code, force=1, ignore_permissions=True)
			frappe.db.commit()

	def test_add_packaging_material_succeeds_and_item_exists(self):
		"""调用 add_packaging_material 能成功添加一个包材（纸箱），且 Item 存在于库中"""
		if not frappe.get_meta("Item").get_field("br_carton_length"):
			self.skipTest('Custom field "br_carton_length" not found on Item (need migrate)')
		if not frappe.db.exists("Warehouse", "半成品 - B"):
			self.skipTest('需要存在仓库「半成品 - B」（BR）以校验包材默认仓库')

		res = add_packaging_material(
			br_carton_length=self.test_length,
			br_carton_width=self.test_width,
			br_carton_height=self.test_height,
			item_name="测试纸箱",
		)
		self.created_item_code = res.get("item_code")

		self.assertIn("item_code", res)
		self.assertTrue(self.created_item_code, "应返回创建后的 item_code")
		self.assertEqual(res["br_carton_length"], self.test_length)
		self.assertEqual(res["br_carton_width"], self.test_width)
		self.assertEqual(res["br_carton_height"], self.test_height)
		self.assertIn("item_name", res)
		self.assertTrue(frappe.db.exists("Item", self.created_item_code), "纸箱 Item 应已写入数据库")

		item = frappe.get_cached_doc("Item", self.created_item_code)
		self.assertEqual(item.br_carton_length, self.test_length)
		self.assertEqual(item.br_carton_width, self.test_width)
		self.assertEqual(item.br_carton_height, self.test_height)
		self.assertEqual(res.get("default_warehouse"), "半成品 - B")
		self.assertEqual(res.get("default_company"), "BR")
		rows = item.get("item_defaults") or []
		self.assertTrue(rows)
		self.assertEqual(rows[0].get("default_warehouse"), "半成品 - B")
		self.assertEqual(rows[0].get("company"), "BR")
