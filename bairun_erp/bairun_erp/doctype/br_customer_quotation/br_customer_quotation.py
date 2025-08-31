# Copyright (c) 2025, guinan.lin@foxmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from datetime import datetime
import random


class BRCustomerQuotation(Document):
	pass


def generate_quotation_number(target_date=None):
	"""
	生成报价单号，格式：YYMMDD-SSSSSR
	其中：
	- YYMMDD: 年月日（6位）
	- SSSSS: 当天第几秒（5位，补零）
	- R: 0-9的随机数（1位）
	"""
	# 1. 获取当前日期（或指定日期）
	if target_date is None:
		target_date = datetime.now()
	
	# 2. 格式化日期为 YYMMDD
	year = str(target_date.year)[-2:]  # 取年份后两位
	month = str(target_date.month).zfill(2)  # 补零到2位
	day = str(target_date.day).zfill(2)  # 补零到2位
	date_string = f"{year}{month}{day}"
	
	# 3. 计算当天第几秒
	start_of_day = target_date.replace(hour=0, minute=0, second=0, microsecond=0)  # 设置为当天00:00:00
	seconds_since_start_of_day = int((target_date - start_of_day).total_seconds())
	seconds_string = str(seconds_since_start_of_day).zfill(5)  # 补零到5位
	
	# 4. 生成0-9的随机数
	random_digit = random.randint(0, 9)
	
	# 5. 组合成最终格式
	return f"{date_string}-{seconds_string}{random_digit}"


@frappe.whitelist()
def copy_quotation(quotation_number):
	"""
	复制客户报价单的白名单方法
	基于最终采纳的版本创建新的报价单
	只有授权用户才能调用此方法
	
	Args:
		quotation_number (str): 报价单单号
	
	Returns:
		str: 新创建的报价单名称
	"""
	# 根据报价单单号获取客户报价单
	customer_quotation = frappe.get_doc("BR Customer Quotation", {
		"quotation_number": quotation_number
	})
	
	if not customer_quotation:
		frappe.throw(f"未找到报价单号为 {quotation_number} 的客户报价单")
	
	if not customer_quotation.is_adopted:
		frappe.throw("只有已采纳的报价单才能进行复制")
	
	if not customer_quotation.adopted_version_id:
		frappe.throw("未找到采纳的版本信息")
	
	# 1. 查找最终采纳的报价单版本
	adopted_quotation = frappe.get_doc("BR Quotation", {
		"quotation_number": quotation_number,
		"version_id": customer_quotation.adopted_version_id
	})
	
	if not adopted_quotation:
		frappe.throw(f"未找到版本 {customer_quotation.adopted_version_id} 的报价单")
	
	# 2. 创建新的报价单（复制主表数据）
	new_quotation = frappe.copy_doc(adopted_quotation)
	
	# 生成新的报价单号
	new_quotation_number = generate_quotation_number()
	new_quotation.quotation_number = new_quotation_number
	
	# 新版本号固定为V0
	new_quotation.version_id = "V0"
	new_quotation.version_name = "复制版本-V0"
	new_quotation.is_adopted = 0  # 新复制的版本未采纳
	new_quotation.adopted_version_id = ""
	new_quotation.adopted_version_name = ""
	new_quotation.adopted_at = None
	new_quotation.adopted_by = None
	new_quotation.adoption_reason = ""
	
	# 3. 保存新的报价单
	new_quotation.insert()
	
	# 4. 复制明细表数据（BR Quotation Details）
	if adopted_quotation.details:
		for detail in adopted_quotation.details:
			new_detail = frappe.copy_doc(detail)
			new_detail.parent = new_quotation.name
			new_detail.parentfield = "details"
			new_detail.parenttype = "BR Quotation"
			new_detail.insert()
	
	# 5. 更新客户报价单的版本信息
	customer_quotation.total_versions = customer_quotation.total_versions + 1 if hasattr(customer_quotation, 'total_versions') else 2
	customer_quotation.save()
	
	frappe.msgprint(f"成功复制报价单，新报价单号: {new_quotation_number}，新版本ID: {new_quotation.version_id}")
	
	return new_quotation.name
