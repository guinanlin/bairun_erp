# Copyright (c) 2026, Bairun and contributors
# Material Issue 类 MR：Item 默认 + set_missing_values 可能把 target warehouse 填成与发料仓相同，
# 导致 BuyingController.validate_from_warehouse 报错。在父类校验前清空重复目标仓。

from __future__ import unicode_literals

from erpnext.stock.doctype.material_request.material_request import MaterialRequest


class BairunMaterialRequest(MaterialRequest):
	def validate_from_warehouse(self):
		if self.material_request_type == "Material Issue":
			for d in self.get("items") or []:
				if d.get("from_warehouse") and d.get("warehouse") and d.from_warehouse == d.warehouse:
					d.warehouse = None
		super().validate_from_warehouse()
