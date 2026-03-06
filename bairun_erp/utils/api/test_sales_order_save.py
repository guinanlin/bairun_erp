# Copyright (c) 2025, Bairun and contributors
# For license information, see license.txt.
"""
销售合同保存接口单元测试。

运行:
    bench --site site1.local run-tests --module bairun_erp.utils.api.test_sales_order_save
"""

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from bairun_erp.utils.api.sales.sales_order import save_sales_order


class TestSaveSalesOrder(FrappeTestCase):
    """测试 save_sales_order 接口"""

    created_sales_orders = []

    def setUp(self):
        super().setUp()
        # 使用站点已有数据
        self._ensure_test_data()

    def tearDown(self):
        pass  # 清理在 tearDownClass

    def _ensure_test_data(self):
        """确保有可用的 Company、Customer，设置 self.company, self.customer"""
        companies = frappe.get_all("Company", limit=1)
        if not companies:
            self.skipTest("No Company in site")
        self.company = companies[0].name
        customers = frappe.get_all("Customer", limit=1)
        if not customers:
            self.skipTest("No Customer in site")
        self.customer = customers[0].name

    def _get_item_codes(self, count=2):
        """获取站点中存在的 Item 编码"""
        items = frappe.get_all("Item", fields=["name"], limit=count)
        if not items:
            self.skipTest("No Item found in site - run erpnext tests first")
        return [r["name"] for r in items]

    @classmethod
    def tearDownClass(cls):
        for so_name in cls.created_sales_orders:
            if frappe.db.exists("Sales Order", so_name):
                try:
                    so = frappe.get_doc("Sales Order", so_name)
                    if so.docstatus == 1:
                        so.cancel()
                    frappe.delete_doc("Sales Order", so_name, force=True, ignore_permissions=True)
                except Exception:
                    pass
        super().tearDownClass()

    def _min_order_data(self, **overrides):
        """最小有效 order_data"""
        items = self._get_item_codes(1)
        pl = frappe.get_all("Price List", filters={"selling": 1}, limit=1)
        selling_pl = pl[0]["name"] if pl else "Standard Selling"
        data = {
            "doctype": "Sales Order",
            "customer": self.customer,
            "company": self.company,
            "order_type": "Sales",
            "transaction_date": nowdate(),
            "delivery_date": add_days(nowdate(), 10),
            "currency": "INR",
            "conversion_rate": 1,
            "selling_price_list": selling_pl,
            "price_list_currency": "INR",
            "plc_conversion_rate": 1,
            "status": "Draft",
            "items": [
                {
                    "item_code": items[0],
                    "qty": 2,
                    "rate": 10.5,
                    "uom": "Nos",
                    "amount": 21,
                }
            ],
            "taxes": [],
        }
        data.update(overrides)
        return data

    def test_save_single_item(self):
        """单行物料，最小必填字段"""
        order_data = self._min_order_data()
        result = save_sales_order(order_data)
        self.assertIn("data", result)
        self.assertTrue(result["data"]["success"])
        self.assertIn("name", result["data"])
        so_name = result["data"]["name"]
        self.assertTrue(frappe.db.exists("Sales Order", so_name))
        TestSaveSalesOrder.created_sales_orders.append(so_name)

    def test_save_bom_scenario(self):
        """BOM 生单：多行不同 item_code，无 variant/attributes"""
        item_codes = self._get_item_codes(2)
        if len(item_codes) < 2:
            item_codes = item_codes + item_codes  # 同一物料两行
        order_data = self._min_order_data(
            custom_material_code_display="成品物料",
            custom_style_number="BOM-001",
            custom_sub_order_type="大货销售合同",
            items=[
                {"item_code": item_codes[0], "qty": 2, "rate": 10.5, "uom": "Nos", "amount": 21},
                {"item_code": item_codes[1], "qty": 4, "rate": 2.5, "uom": "Nos", "amount": 10},
            ],
        )
        result = save_sales_order(order_data)
        self.assertIn("data", result)
        self.assertTrue(result["data"]["success"])
        self.assertIn("name", result["data"])
        so_name = result["data"]["name"]
        so = frappe.get_doc("Sales Order", so_name)
        self.assertEqual(len(so.items), 2)
        TestSaveSalesOrder.created_sales_orders.append(so_name)

    def test_invalid_customer(self):
        """customer 不存在"""
        order_data = self._min_order_data(customer="__NonexistentCustomer__")
        result = save_sales_order(order_data)
        self.assertIn("error", result)
        self.assertIn("不存在", result["error"])

    def test_empty_items(self):
        """items 为空"""
        order_data = self._min_order_data(items=[])
        result = save_sales_order(order_data)
        self.assertIn("error", result)
        self.assertIn("至少需要一个产品明细", result["error"])

    def test_item_not_exists(self):
        """某行 item_code 不存在"""
        order_data = self._min_order_data(
            items=[
                {"item_code": "__NonexistentItem__", "qty": 1, "rate": 10, "uom": "Nos"},
            ],
        )
        result = save_sales_order(order_data)
        self.assertIn("error", result)
        self.assertIn("不存在", result["error"])

    def test_json_data_wrapper(self):
        """请求为 { "json_data": { "order_data": {...} } }"""
        order_data = self._min_order_data()
        result = save_sales_order(None, json_data={"order_data": order_data})
        self.assertIn("data", result)
        self.assertTrue(result["data"]["success"])
        TestSaveSalesOrder.created_sales_orders.append(result["data"]["name"])

    def test_update_existing(self):
        """传入已存在的 name，更新"""
        order_data = self._min_order_data()
        create_result = save_sales_order(order_data)
        self.assertTrue(create_result["data"]["success"])
        so_name = create_result["data"]["name"]
        TestSaveSalesOrder.created_sales_orders.append(so_name)

        # 更新
        order_data["name"] = so_name
        order_data["items"][0]["qty"] = 5
        order_data["items"][0]["amount"] = 52.5
        update_result = save_sales_order(order_data)
        self.assertTrue(update_result["data"]["success"])
        self.assertEqual(update_result["data"]["name"], so_name)
        so = frappe.get_doc("Sales Order", so_name)
        self.assertEqual(so.items[0].qty, 5)
