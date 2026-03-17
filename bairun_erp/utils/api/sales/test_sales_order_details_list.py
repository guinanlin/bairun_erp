# Copyright (c) 2025, Bairun and contributors
# 销售明细列表白名单接口单元测试。
#
# 运行:
#   bench --site <site> run-tests --module bairun_erp.utils.api.sales.test_sales_order_details_list

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from bairun_erp.utils.api.sales.sales_order_details_list import get_sales_order_details_list


class TestSalesOrderDetailsList(FrappeTestCase):
    """测试 get_sales_order_details_list 销售明细列表接口"""

    def test_response_structure(self):
        """无参数调用：响应应包含 data（列表）和 total（整数）"""
        result = get_sales_order_details_list(
            json_data={"limit_start": 0, "limit_page_length": 20}
        )
        self.assertIsInstance(result, dict)
        self.assertIn("data", result)
        self.assertIn("total", result)
        self.assertIsInstance(result["data"], list)
        self.assertIsInstance(result["total"], int)
        self.assertGreaterEqual(result["total"], 0)

    def test_empty_params(self):
        """空 json_data：使用默认分页，不报错"""
        result = get_sales_order_details_list()
        self.assertIsInstance(result, dict)
        self.assertIn("data", result)
        self.assertIn("total", result)
        self.assertIsInstance(result["data"], list)
        self.assertIsInstance(result["total"], int)

    def test_pagination(self):
        """分页参数：limit_start、limit_page_length 生效"""
        result = get_sales_order_details_list(
            json_data={"limit_start": 0, "limit_page_length": 5}
        )
        data = result["data"]
        total = result["total"]
        self.assertLessEqual(len(data), 5)
        self.assertGreaterEqual(total, 0)
        if total > 0:
            self.assertGreaterEqual(total, len(data))

    def test_search_params_no_error(self):
        """传入筛选参数不报错，返回列表与总数"""
        result = get_sales_order_details_list(
            json_data={
                "limit_start": 0,
                "limit_page_length": 10,
                "customer_name_search": "客户",
                "order_date_from": "2020-01-01",
                "order_date_to": "2030-12-31",
            }
        )
        self.assertIn("data", result)
        self.assertIn("total", result)
        self.assertIsInstance(result["data"], list)
        self.assertIsInstance(result["total"], int)

    def test_row_structure_when_has_data(self):
        """若有数据，每行应包含约定字段"""
        result = get_sales_order_details_list(
            json_data={"limit_start": 0, "limit_page_length": 1}
        )
        data = result["data"]
        if not data:
            self.skipTest("站点无销售订单数据，跳过行结构校验")
        row = data[0]
        required = [
            "name", "transaction_date", "customer", "customer_name",
            "grand_total", "total_qty", "item_names", "delivered_qty", "outstanding_qty",
        ]
        for key in required:
            self.assertIn(key, row, "销售明细行应包含字段: {}".format(key))
        self.assertGreaterEqual(row["outstanding_qty"], 0)
        self.assertGreaterEqual(
            flt(row.get("delivered_qty")),
            0,
            "delivered_qty 应为非负数",
        )

    def test_order_by_param(self):
        """传入 order_by 不报错"""
        result = get_sales_order_details_list(
            json_data={
                "order_by": "transaction_date desc",
                "limit_start": 0,
                "limit_page_length": 5,
            }
        )
        self.assertIn("data", result)
        self.assertIn("total", result)
