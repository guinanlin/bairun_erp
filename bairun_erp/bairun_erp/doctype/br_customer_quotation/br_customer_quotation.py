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
	adopted_quotation_name = frappe.db.get_value(
		"BR Quotation", 
		{
			"quotation_number": quotation_number,
			"version_id": customer_quotation.adopted_version_id
		},
		"name"
	)
	
	if not adopted_quotation_name:
		frappe.throw(f"未找到版本 {customer_quotation.adopted_version_id} 的报价单")
	
	print(f"[DEBUG] 找到采纳版本报价单: {adopted_quotation_name}")
	
	adopted_quotation = frappe.get_doc("BR Quotation", adopted_quotation_name)
	
	if not adopted_quotation:
		frappe.throw(f"未找到版本 {customer_quotation.adopted_version_id} 的报价单")
	
	print(f"[DEBUG] 采纳版本报价单明细数量: {len(adopted_quotation.details) if adopted_quotation.details else 0}")
	
	# 2. 创建新的报价单（复制主表数据）
	new_quotation = frappe.copy_doc(adopted_quotation)
	
	# 生成新的报价单号
	new_quotation_number = generate_quotation_number()
	new_quotation.quotation_number = new_quotation_number
	
	# 新版本号固定为V0
	new_quotation.version_id = "V0"
	new_quotation.version_name = "V0"
	new_quotation.active_version_id = "V0"
	new_quotation.is_adopted = 0  # 新复制的版本未采纳
	new_quotation.adopted_version_id = ""
	new_quotation.adopted_version_name = ""
	new_quotation.adopted_at = None
	new_quotation.adopted_by = None
	new_quotation.adoption_reason = ""
	
	# 临时禁用同步机制，避免重复操作
	new_quotation.flags.disable_sync = True
	
	# 3. 保存新的报价单（这会自动保存所有复制的明细）
	new_quotation.insert()
	
	# 注意：不需要手动复制明细，因为 frappe.copy_doc() 已经自动复制了所有子表数据
	print(f"[DEBUG] 报价单创建完成，明细数量: {len(new_quotation.details) if new_quotation.details else 0}")
	
	# 4. 创建新的客户报价单记录
	try:
		new_customer_quotation = frappe.get_doc({
			"doctype": "BR Customer Quotation",
			"quotation_number": new_quotation_number,
			"customer_name": adopted_quotation.customer_name,
			"product_name": adopted_quotation.product_name,
			"is_adopted": 0,  # 新复制的版本未采纳
			"adopted_version_id": "",
			"adopted_version_name": "",
			"adopted_at": None,
			"adopted_by": None,
			"adoption_reason": "",
			"total_versions": 1
		})
		
		new_customer_quotation.insert(ignore_permissions=True)
		print(f"[DEBUG] 成功创建新的客户报价单记录: {new_customer_quotation.name}")
		
	except Exception as e:
		print(f"[DEBUG] 创建客户报价单失败: {str(e)}")
		frappe.log_error(f"创建客户报价单失败: {str(e)}", "BR Customer Quotation Copy Error")
	
	# 5. 更新原客户报价单的版本信息
	customer_quotation.total_versions = customer_quotation.total_versions + 1 if hasattr(customer_quotation, 'total_versions') else 2
	customer_quotation.save()
	
	frappe.msgprint(f"成功复制报价单，新报价单号: {new_quotation_number}，新版本ID: {new_quotation.version_id}")
	
	return new_quotation.name
