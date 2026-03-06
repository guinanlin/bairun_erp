# -*- coding: utf-8 -*-
"""复现用户提供的两次插入数据"""
from __future__ import unicode_literals

import json
import frappe


def run_test():
	frappe.connect()
	tree1 = {
		"id": "root",
		"text": "原料_mm2x8w8gi1na",
		"label": "原料_mm2x8w8gi1na",
		"item_name": "原料_mm2x8w8gi1na",
		"warehouse": "成品仓库",
		"item_group": "成品",
		"bom_qty": 1,
		"children": [
			{
				"id": "node-test-1",
				"text": "垫片_mm2x8w8g77ea",
				"label": "垫片_mm2x8w8g77ea",
				"item_name": "垫片_mm2x8w8g77ea",
				"warehouse": "半成品仓库",
				"item_group": "半成品",
				"bom_qty": 1,
				"children": [
					{
						"id": "node-test-2",
						"text": "毛坯件_mm2x8w8geozp",
						"label": "毛坯件_mm2x8w8geozp",
						"item_name": "毛坯件_mm2x8w8geozp",
						"warehouse": "毛坯仓库",
						"item_group": "毛坯",
						"bom_qty": 1,
						"children": [
							{
								"id": "node-test-3",
								"text": "衬套_mm2x8w8gkab4",
								"label": "衬套_mm2x8w8gkab4",
								"item_name": "衬套_mm2x8w8gkab4",
								"warehouse": "毛坯仓库",
								"item_group": "毛坯",
								"bom_qty": 1
							}
						]
					}
				]
			}
		]
	}

	tree2 = {
		"id": "root",
		"text": "原料_mm2x8w8gi1na",
		"label": "原料_mm2x8w8gi1na",
		"item_name": "原料_mm2x8w8gi1na",
		"warehouse": "成品仓库",
		"item_group": "成品",
		"bom_qty": 1,
		"children": [
			{
				"id": "node-test-1",
				"text": "垫片_mm2x8w8g77ea",
				"label": "垫片_mm2x8w8g77ea",
				"item_name": "垫片_mm2x8w8g77ea",
				"warehouse": "半成品仓库",
				"item_code": "垫片_mm2x8w8g77ea",
				"item_group": "半成品",
				"bom_qty": 2,
				"children": [
					{
						"id": "node-test-2",
						"text": "毛坯件_mm2x8w8geozp",
						"label": "毛坯件_mm2x8w8geozp",
						"item_name": "毛坯件_mm2x8w8geozp",
						"warehouse": "毛坯仓库",
						"item_group": "毛坯",
						"bom_qty": 1,
						"children": [
							{
								"id": "node-test-3",
								"text": "衬套_mm2x8w8gkab4",
								"label": "衬套_mm2x8w8gkab4",
								"item_name": "衬套_mm2x8w8gkab4",
								"warehouse": "毛坯仓库",
								"item_group": "毛坯",
								"bom_qty": 1
							}
						]
					}
				]
			},
			{
				"id": "node-1772077603925",
				"text": "test0987",
				"label": "test0987",
				"item_name": "test0987",
				"bom_qty": 1
			}
		]
	}

	from bairun_erp.utils.api.material.bom_item import create_bom_from_canvas_tree

	print("\n=== 第一次插入 ===")
	try:
		res1 = create_bom_from_canvas_tree(json.dumps(tree1, ensure_ascii=False))
		frappe.db.commit()
		print("step1:", res1["step1_complete"], "step2:", res1["step2_complete"])
		for i in res1["items"]:
			print("  item:", i["node_id"], i["item_code"], "->", i["status"])
		for b in res1["boms"]:
			print("  bom:", b["parent_item_code"], "->", b["status"])
	except Exception as e:
		print("ERROR:", e)
		import traceback
		traceback.print_exc()
		return

	print("\n=== 第二次插入 ===")
	try:
		res2 = create_bom_from_canvas_tree(json.dumps(tree2, ensure_ascii=False))
		frappe.db.commit()
		print("step1:", res2["step1_complete"], "step2:", res2["step2_complete"])
		for i in res2["items"]:
			print("  item:", i["node_id"], i["item_code"], "->", i["status"], i.get("error", ""))
		for b in res2["boms"]:
			print("  bom:", b["parent_item_code"], b.get("bom_no"), "->", b["status"], b.get("error", ""))
	except Exception as e:
		print("ERROR:", e)
		import traceback
		traceback.print_exc()
	finally:
		frappe.destroy()


if __name__ == "__main__":
	run_test()
