# Copyright (c) 2026, Bairun ERP and contributors
# For license information, see license.txt

from frappe.model.document import Document
from frappe.utils import flt


class BRItemCostDetail(Document):
	def validate(self):
		self._set_derived_cost_fields()
		self._set_audit_defaults()

	def _set_derived_cost_fields(self):
		"""克单价×重量=材料成本；周期1小时固定为 3600 秒（用于产量换算）。"""
		self.br_seconds_per_hour = 3600
		price = flt(self.br_price_per_gram)
		weight = flt(self.br_weight_grams)
		self.br_material_cost_yuan = round(price * weight, 4)

	def _set_audit_defaults(self):
		"""审核状态默认未审核，避免空值影响前端展示。"""
		if not (self.br_audit_status or "").strip():
			self.br_audit_status = "未审核"
