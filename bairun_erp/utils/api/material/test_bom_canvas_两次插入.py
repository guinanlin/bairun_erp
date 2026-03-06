# -*- coding: utf-8 -*-
"""
测试：create_bom_from_canvas_tree 两次插入
1. 第一次：创建一套 BOM（Item + BOM）
2. 第二次：对同一结构做小幅改动后再次插入，应视为 BOM 版本升级
   - Item 应全部 existed
   - BOM 应全部 success（新建版本）
"""
from __future__ import unicode_literals

import json
import frappe


def run_test():
	frappe.connect()
	try:
		# 使用唯一前缀避免与已有数据冲突
		prefix = "TEST-BOM-V2"
		tree1 = {
			"id": "root",
			"item_name": prefix + "-成品",
			"item_group": "成品",
			"bom_qty": 1,
			"children": [
				{
					"id": "node-1",
					"item_name": prefix + "-半成品",
					"item_group": "半成品",
					"bom_qty": 1,
					"children": [
						{
							"id": "node-2",
							"item_name": prefix + "-毛坯",
							"item_group": "毛坯",
							"bom_qty": 1,
							"children": [],
						}
					],
				}
			],
		}

		from bairun_erp.utils.api.material.bom_item import create_bom_from_canvas_tree

		# === 第一次插入 ===
		print("\n" + "=" * 60)
		print("【第一次插入】创建 BOM")
		print("=" * 60)
		res1 = create_bom_from_canvas_tree(json.dumps(tree1, ensure_ascii=False))
		frappe.db.commit()
		print("step1_complete:", res1.get("step1_complete"))
		print("step2_complete:", res1.get("step2_complete"))
		print("items:")
		for i in res1.get("items", []):
			print("  ", i.get("node_id"), i.get("item_code"), "->", i.get("status"))
		print("boms:")
		for b in res1.get("boms", []):
			print("  ", b.get("parent_item_code"), b.get("bom_no"), "->", b.get("status"))

		# === 第二次插入（同一结构，做小幅改动：bom_qty 改为 2）===
		tree2 = {
			"id": "root",
			"item_name": prefix + "-成品",
			"item_group": "成品",
			"bom_qty": 1,
			"children": [
				{
					"id": "node-1",
					"item_name": prefix + "-半成品",
					"item_group": "半成品",
					"bom_qty": 2,  # 改动：半成品用量从 1 改为 2
					"children": [
						{
							"id": "node-2",
							"item_name": prefix + "-毛坯",
							"item_group": "毛坯",
							"bom_qty": 1,
							"children": [],
						}
					],
				}
			],
		}

		print("\n" + "=" * 60)
		print("【第二次插入】BOM 版本升级（物料应 existed，BOM 应新建）")
		print("=" * 60)
		res2 = create_bom_from_canvas_tree(json.dumps(tree2, ensure_ascii=False))
		frappe.db.commit()
		print("step1_complete:", res2.get("step1_complete"))
		print("step2_complete:", res2.get("step2_complete"))
		print("items:")
		for i in res2.get("items", []):
			print("  ", i.get("node_id"), i.get("item_code"), "->", i.get("status"))
		print("boms:")
		for b in res2.get("boms", []):
			print("  ", b.get("parent_item_code"), b.get("bom_no"), "->", b.get("status"))

		# === 校验 ===
		errors = []
		if not res2.get("step1_complete"):
			errors.append("第二次 step1_complete 应为 True")
		if not res2.get("step2_complete"):
			errors.append("第二次 step2_complete 应为 True")
		for i in res2.get("items", []):
			if i.get("status") != "existed":
				errors.append("第二次 items 应全部 existed，实际: {} -> {}".format(i.get("item_code"), i.get("status")))
		for b in res2.get("boms", []):
			if b.get("status") != "success":
				errors.append("第二次 boms 应全部 success，实际: {} -> {}".format(b.get("parent_item_code"), b.get("status")))

		if errors:
			print("\n❌ 测试失败:")
			for e in errors:
				print("  -", e)
			return False
		print("\n✅ 测试通过：两次插入均成功，第二次物料 existed，BOM 新建版本")
		return True
	finally:
		frappe.destroy()


if __name__ == "__main__":
	run_test()
