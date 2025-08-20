# Copyright (c) 2025, guinan.lin@foxmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe import _


class BRQuotation(Document):
	def after_insert(self):
		"""插入后处理 - 同步保存到客户报价单"""
		self._sync_to_customer_quotation()
	
	def on_update(self):
		"""更新后处理 - 同步保存到客户报价单"""
		self._sync_to_customer_quotation()
	
	def _sync_to_customer_quotation(self):
		"""同步保存到客户报价单"""
		try:
			# 检查报价单号和客户名称是否存在
			if not self.quotation_number or not self.customer_name:
				print(f"[DEBUG] 报价单号或客户名称为空，跳过同步")
				return
			
			# 检查是否已存在相同的报价单号
			existing_customer_quotation = frappe.db.exists(
				"BR Customer Quotation", 
				{"quotation_number": self.quotation_number}
			)
			
			if existing_customer_quotation:
				print(f"[DEBUG] 报价单号 {self.quotation_number} 已存在于客户报价单中，更新产品名称")
				# 更新现有的客户报价单
				customer_quotation_doc = frappe.get_doc("BR Customer Quotation", existing_customer_quotation)
				customer_quotation_doc.product_name = self.product_name
				customer_quotation_doc.save(ignore_permissions=True)
				print(f"[DEBUG] 成功更新客户报价单 {self.quotation_number} 的产品名称")
				return
			
			# 创建新的客户报价单
			customer_quotation_doc = frappe.get_doc({
				"doctype": "BR Customer Quotation",
				"quotation_number": self.quotation_number,
				"customer_name": self.customer_name,
				"product_name": self.product_name
			})
			
			customer_quotation_doc.insert(ignore_permissions=True)
			print(f"[DEBUG] 成功同步报价单 {self.quotation_number} 到客户报价单")
			
		except Exception as e:
			print(f"[DEBUG] 同步到客户报价单失败: {str(e)}")
			frappe.log_error(f"同步报价单到客户报价单失败: {str(e)}", "BR Quotation Sync Error")


def build_order_clause(order_by, order_direction, valid_fields):
	"""
	构建排序子句
	:param order_by: 排序字段，支持多字段，用逗号分隔
	:param order_direction: 排序方向，支持多方向，用逗号分隔
	:param valid_fields: 有效字段列表
	:return: ORDER BY 子句
	"""
	if not order_by:
		return "ORDER BY creation DESC"
	
	# 分割字段和方向
	order_fields = [field.strip() for field in str(order_by).split(',')]
	order_directions = [direction.strip() for direction in str(order_direction).split(',')]
	
	# 构建排序项列表
	order_items = []
	
	for i, field in enumerate(order_fields):
		# 验证字段有效性
		if field not in valid_fields:
			continue
		
		# 获取对应的排序方向
		if i < len(order_directions):
			direction = order_directions[i].upper()
		else:
			# 如果方向数量不足，使用最后一个方向
			direction = order_directions[-1].upper() if order_directions else "DESC"
		
		# 验证方向有效性
		if direction not in ['ASC', 'DESC']:
			direction = "DESC"
		
		order_items.append(f"{field} {direction}")
	
	# 如果没有有效的排序项，使用默认排序
	if not order_items:
		return "ORDER BY creation DESC"
	
	return f"ORDER BY {', '.join(order_items)}"


@frappe.whitelist()
def get_quotation_list(page=1, page_size=20, filters=None, order_by="creation", order_direction="desc"):
	"""
	获取报价单列表，支持分页
	新的数据层级结构：
	BR Customer Quotation → BR Quotation → BR Quotation Details
	
	:param page: 页码，从1开始
	:param page_size: 每页数量
	:param filters: 过滤条件
	:param order_by: 排序字段，支持多字段排序，如 "quotation_date,creation"
	:param order_direction: 排序方向 (asc/desc)，支持多方向，如 "desc,asc"
	:return: 包含客户报价单列表和分页信息的字典
	"""
	print(f"[DEBUG] get_quotation_list 开始执行")
	print(f"[DEBUG] 参数: page={page}, page_size={page_size}, filters={filters}, order_by={order_by}, order_direction={order_direction}")
	
	try:
		# 设置默认过滤条件
		if not filters:
			filters = {}
		
		print(f"[DEBUG] 过滤条件: {filters}")
		
		# 计算偏移量
		offset = (int(page) - 1) * int(page_size)
		print(f"[DEBUG] 分页参数: offset={offset}, page_size={page_size}")
		
		# 构建查询条件 - 从BR Customer Quotation开始
		conditions = "WHERE 1=1"
		params = []
		
		print(f"[DEBUG] 开始构建查询条件")
		
		# 添加过滤条件
		if filters.get('customer_name'):
			conditions += " AND customer_name LIKE %s"
			params.append(f"%{filters['customer_name']}%")
			print(f"[DEBUG] 添加客户名称过滤: {filters['customer_name']}")
		
		if filters.get('product_name'):
			conditions += " AND product_name LIKE %s"
			params.append(f"%{filters['product_name']}%")
			print(f"[DEBUG] 添加产品名称过滤: {filters['product_name']}")
		
		if filters.get('quotation_number'):
			conditions += " AND quotation_number LIKE %s"
			params.append(f"%{filters['quotation_number']}%")
			print(f"[DEBUG] 添加报价单号过滤: {filters['quotation_number']}")
		
		print(f"[DEBUG] 查询条件: {conditions}")
		print(f"[DEBUG] 查询参数: {params}")
		
		# 验证并构建排序条件
		valid_order_fields = [
			'name', 'quotation_number', 'customer_name', 'product_name', 'creation', 'modified'
		]
		
		order_clause = build_order_clause(order_by, order_direction, valid_order_fields)
		print(f"[DEBUG] 排序子句: {order_clause}")
		
		# 查询客户报价单总数
		count_sql = f"""
			SELECT COUNT(*) as total
			FROM `tabBR Customer Quotation`
			{conditions}
		"""
		print(f"[DEBUG] 总数查询SQL: {count_sql}")
		print(f"[DEBUG] 总数查询参数: {params}")
		
		total_count = frappe.db.sql(count_sql, params, as_dict=True)[0]['total']
		print(f"[DEBUG] 查询到的客户报价单总数: {total_count}")
		
		# 如果没有数据，先检查表是否存在数据
		if total_count == 0:
			print(f"[DEBUG] 没有找到客户报价单数据，检查表结构...")
			check_sql = "SELECT COUNT(*) as total FROM `tabBR Customer Quotation`"
			all_count = frappe.db.sql(check_sql, as_dict=True)[0]['total']
			print(f"[DEBUG] 客户报价单表中总记录数: {all_count}")
			
			if all_count > 0:
				print(f"[DEBUG] 表中有数据，但查询条件可能有问题")
				sample_sql = "SELECT name, quotation_number, customer_name, product_name FROM `tabBR Customer Quotation` LIMIT 5"
				sample_data = frappe.db.sql(sample_sql, as_dict=True)
				print(f"[DEBUG] 样本数据: {sample_data}")
		
		# 查询客户报价单列表
		customer_quotation_sql = f"""
			SELECT 
				name,
				quotation_number,
				customer_name,
				product_name,
				creation,
				modified
			FROM `tabBR Customer Quotation`
			{conditions}
			{order_clause}
			LIMIT %s OFFSET %s
		"""
		params.extend([int(page_size), offset])
		
		print(f"[DEBUG] 客户报价单查询SQL: {customer_quotation_sql}")
		print(f"[DEBUG] 客户报价单查询参数: {params}")
		
		customer_quotations = frappe.db.sql(customer_quotation_sql, params, as_dict=True)
		print(f"[DEBUG] 查询到的客户报价单数量: {len(customer_quotations)}")
		print(f"[DEBUG] 客户报价单数据: {customer_quotations}")
		
		# 获取每个客户报价单的版本信息
		print(f"[DEBUG] 开始获取版本数据")
		for i, customer_quotation in enumerate(customer_quotations):
			print(f"[DEBUG] 处理第 {i+1} 个客户报价单: {customer_quotation.get('quotation_number', 'N/A')}")
			
			# 获取该报价单号的所有版本
			versions = get_quotation_versions(customer_quotation['quotation_number'])
			customer_quotation['versions'] = versions
			print(f"[DEBUG] 客户报价单 {customer_quotation.get('quotation_number', 'N/A')} 的版本数量: {len(versions)}")
		
		# 计算分页信息
		total_pages = (total_count + int(page_size) - 1) // int(page_size)
		print(f"[DEBUG] 分页信息: total_pages={total_pages}")
		
		result = {
			'status': 'success',
			'data': {
				'customer_quotations': customer_quotations,
				'pagination': {
					'current_page': int(page),
					'page_size': int(page_size),
					'total_count': total_count,
					'total_pages': total_pages,
					'has_next': int(page) < total_pages,
					'has_prev': int(page) > 1
				}
			}
		}
		
		print(f"[DEBUG] 返回结果: {result}")
		return result
		
	except Exception as e:
		print(f"[DEBUG] 发生异常: {str(e)}")
		frappe.log_error(f"获取客户报价单列表失败: {str(e)}", "BR Customer Quotation API Error")
		return {
			'status': 'error',
			'message': f'获取客户报价单列表失败: {str(e)}'
		}


def get_quotation_versions(quotation_number):
	"""
	获取指定报价单号的所有版本
	:param quotation_number: 报价单号
	:return: 版本列表
	"""
	print(f"[DEBUG] get_quotation_versions 开始执行，报价单号: {quotation_number}")
	try:
		versions_sql = """
			SELECT 
				name,
				quotation_number,
				customer_name,
				quotation_date,
				validity_period,
				include_tax,
				tax_rate,
				profit_rate,
				show_full_name,
				uploaded_image,
				material_config,
				total_mold_cost,
				total_cost,
				total_quotation,
				total_profit,
				item_count,
				version_id,
				version_name,
				active_version_id,
				total_versions,
				docstatus,
				creation,
				modified
			FROM `tabBR Quotation`
			WHERE quotation_number = %s
			ORDER BY version_id, creation DESC
		"""
		
		print(f"[DEBUG] 版本查询SQL: {versions_sql}")
		print(f"[DEBUG] 版本查询参数: {quotation_number}")
		
		versions = frappe.db.sql(versions_sql, (quotation_number,), as_dict=True)
		print(f"[DEBUG] 查询到的版本数量: {len(versions)}")
		print(f"[DEBUG] 版本数据: {versions}")
		
		# 获取每个版本的明细行
		print(f"[DEBUG] 开始获取版本明细行数据")
		for i, version in enumerate(versions):
			print(f"[DEBUG] 处理第 {i+1} 个版本: {version.get('name', 'N/A')}")
			version['details'] = get_quotation_details(version['name'])
			print(f"[DEBUG] 版本 {version.get('name', 'N/A')} 的明细行数量: {len(version['details'])}")
		
		return versions
		
	except Exception as e:
		print(f"[DEBUG] get_quotation_versions 发生异常: {str(e)}")
		frappe.log_error(f"获取报价单版本失败: {str(e)}", "BR Quotation Versions API Error")
		return []


def get_quotation_details(quotation_name):
	"""
	获取报价单明细行
	:param quotation_name: 报价单名称
	:return: 明细行列表
	"""
	print(f"[DEBUG] get_quotation_details 开始执行，报价单名称: {quotation_name}")
	try:
		details_sql = """
			SELECT 
				name,
				parent,
				idx,
				part_name,
				full_name,
				material,
				process_type,
				unit_weight,
				output,
				cycle,
				daily_production,
				mold_cost,
				daily_processing_fee,
				blank_processing_fee,
				raw_material_price,
				product_material_price,
				injection_price,
				cost_total,
				quotation_total,
				profit,
				selected_processes,
				process_workstations
			FROM `tabBR Quotation Details`
			WHERE parent = %s
			ORDER BY idx
		"""
		
		print(f"[DEBUG] 明细查询SQL: {details_sql}")
		print(f"[DEBUG] 明细查询参数: {quotation_name}")
		
		details = frappe.db.sql(details_sql, (quotation_name,), as_dict=True)
		print(f"[DEBUG] 查询到的明细行数量: {len(details)}")
		print(f"[DEBUG] 明细行数据: {details}")
		
		return details
		
	except Exception as e:
		print(f"[DEBUG] get_quotation_details 发生异常: {str(e)}")
		frappe.log_error(f"获取报价单明细失败: {str(e)}", "BR Quotation Details API Error")
		return []


@frappe.whitelist()
def get_quotation_by_id(quotation_name):
	"""
	根据ID获取单个报价单的完整信息
	:param quotation_name: 报价单名称
	:return: 报价单完整信息
	"""
	try:
		# 获取报价单主信息
		quotation_sql = """
			SELECT 
				name,
				quotation_number,
				customer_name,
				quotation_date,
				validity_period,
				include_tax,
				tax_rate,
				profit_rate,
				show_full_name,
				uploaded_image,
				material_config,
				total_mold_cost,
				total_cost,
				total_quotation,
				total_profit,
				item_count,
				version_id,
				version_name,
				active_version_id,
				total_versions,
				docstatus,
				creation,
				modified
			FROM `tabBR Quotation`
			WHERE name = %s
		"""
		
		quotation = frappe.db.sql(quotation_sql, (quotation_name,), as_dict=True)
		
		if not quotation:
			return {
				'status': 'error',
				'message': '报价单不存在'
			}
		
		quotation = quotation[0]
		
		# 获取明细行
		quotation['details'] = get_quotation_details(quotation_name)
		
		return {
			'status': 'success',
			'data': quotation
		}
		
	except Exception as e:
		frappe.log_error(f"获取报价单详情失败: {str(e)}", "BR Quotation API Error")
		return {
			'status': 'error',
			'message': f'获取报价单详情失败: {str(e)}'
		}
