# Copyright (c) 2025, Bairun and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from bairun_erp.utils.api.material.item_properties_update import update_item_properties_by_item_code

TEST_ITEM_CODE = "test005"


class TestUpdateItemPropertiesByItemCode(FrappeTestCase):
	def test_missing_item_code(self):
		res = update_item_properties_by_item_code(item_code="", json_data={"item_name": "X"})
		self.assertFalse(res["success"])
		self.assertIn("item_code", res["message"])

	def test_item_not_found(self):
		res = update_item_properties_by_item_code(
			item_code="__nonexistent_item_xyz__",
			json_data={"item_name": "N"},
		)
		self.assertFalse(res["success"])
		self.assertIn("不存在", res["message"])

	def test_no_whitelist_fields(self):
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		res = update_item_properties_by_item_code(
			item_code=TEST_ITEM_CODE,
			json_data={"description": "only non-allowed"},
		)
		self.assertFalse(res["success"])
		self.assertIn("没有可更新", res["message"])

	def test_negative_br_price(self):
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		res = update_item_properties_by_item_code(
			item_code=TEST_ITEM_CODE,
			json_data={"br_price": -1},
		)
		self.assertFalse(res["success"])
		self.assertIn("br_price", res["message"])

	def test_invalid_br_packing_qty(self):
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		res = update_item_properties_by_item_code(
			item_code=TEST_ITEM_CODE,
			json_data={"br_packing_qty": 0},
		)
		self.assertFalse(res["success"])
		self.assertIn("br_packing_qty", res["message"])

	def test_success_updates_item_name(self):
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		item = frappe.get_doc("Item", TEST_ITEM_CODE)
		original = item.item_name
		suffix = " (upd-test)"
		new_name = (original + suffix) if suffix not in (original or "") else original
		res = update_item_properties_by_item_code(item_code=TEST_ITEM_CODE, item_name=new_name)
		self.assertTrue(res.get("success"), msg=res)
		self.assertEqual(res.get("data", {}).get("item_code"), TEST_ITEM_CODE)
		item.reload()
		self.assertEqual(item.item_name, new_name)
		# restore
		item.item_name = original
		item.save()

	def test_item_code_inside_json_data(self):
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		item = frappe.get_doc("Item", TEST_ITEM_CODE)
		original = item.item_name
		new_name = original + " (json-code)"
		res = update_item_properties_by_item_code(
			json_data={"item_code": TEST_ITEM_CODE, "item_name": new_name},
		)
		self.assertTrue(res.get("success"), msg=res)
		item.reload()
		self.assertEqual(item.item_name, new_name)
		item.item_name = original
		item.save()

	def test_nested_item_attrs_merged_like_canvas_node(self):
		"""画布常把属性放在 item_attrs；须与 create_bom Step1 一样能写入白名单字段。"""
		if not frappe.db.exists("Item", TEST_ITEM_CODE):
			self.skipTest(f"Item {TEST_ITEM_CODE} does not exist in DB")
		item = frappe.get_doc("Item", TEST_ITEM_CODE)
		orig_qty = item.get("br_packing_qty")
		res = update_item_properties_by_item_code(
			item_code=TEST_ITEM_CODE,
			json_data={"item_attrs": {"br_packing_qty": 99}},
		)
		self.assertTrue(res.get("success"), msg=res)
		item.reload()
		self.assertEqual(flt(item.get("br_packing_qty")), 99.0)
		item.br_packing_qty = orig_qty
		item.save()
