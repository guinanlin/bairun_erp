# BR Quotation 版本号查询功能

## 概述

`get_quotation_list` 函数现在支持根据 `version_id` 进行查询和排序。

## 功能特性

### 1. 按版本号过滤

可以通过 `filters` 参数中的 `version_id` 来过滤报价单：

```python
# 查询包含特定版本号的报价单
result = get_quotation_list(
    page=1,
    page_size=20,
    filters={"version_id": "V1"}
)

# 查询包含多个版本号的报价单（模糊匹配）
result = get_quotation_list(
    page=1,
    page_size=20,
    filters={"version_id": "V"}
)
```

### 2. 按版本号排序

可以通过 `order_by` 和 `order_direction` 参数按版本号排序：

```python
# 按版本号降序排序
result = get_quotation_list(
    page=1,
    page_size=20,
    order_by="version_id",
    order_direction="desc"
)

# 按版本号升序排序
result = get_quotation_list(
    page=1,
    page_size=20,
    order_by="version_id",
    order_direction="asc"
)

# 多字段排序（版本号 + 创建时间）
result = get_quotation_list(
    page=1,
    page_size=20,
    order_by="version_id,creation",
    order_direction="desc,desc"
)
```

### 3. 组合查询

可以同时使用版本号过滤和其他过滤条件：

```python
# 组合查询：客户名称 + 版本号
result = get_quotation_list(
    page=1,
    page_size=20,
    filters={
        "customer_name": "测试客户",
        "version_id": "V1"
    },
    order_by="version_id",
    order_direction="desc"
)
```

## 数据层级结构

```
BR Customer Quotation (客户报价单)
├── BR Quotation (报价单版本)
│   ├── version_id (版本号)
│   └── BR Quotation Details (报价单明细)
```

## 注意事项

1. **版本号过滤**：当使用 `version_id` 过滤时，系统会先查询 `BR Quotation` 表找到符合条件的所有报价单号，然后在 `BR Customer Quotation` 表中查询对应的客户报价单。

2. **版本号排序**：当使用 `version_id` 排序时，系统会获取所有版本数据后，按第一个版本的版本号进行排序。

3. **模糊匹配**：版本号过滤支持模糊匹配，使用 `LIKE` 查询。

4. **性能考虑**：由于需要查询多个表，版本号过滤和排序可能会比普通查询稍慢。

## 测试

运行测试用例：

```bash
bench --site your-site.com run-tests --module bairun_erp.bairun_erp.doctype.br_quotation.test_br_quotation
```

## 示例响应

```json
{
  "status": "success",
  "data": {
    "customer_quotations": [
      {
        "name": "BR-CQ-001",
        "quotation_number": "TEST-001",
        "customer_name": "测试客户",
        "product_name": "测试产品",
        "creation": "2025-01-01 10:00:00",
        "modified": "2025-01-01 10:00:00",
        "versions": [
          {
            "name": "BR-Q-001",
            "version_id": "V1",
            "version_name": "版本1",
            "details": [...]
          },
          {
            "name": "BR-Q-002",
            "version_id": "V2",
            "version_name": "版本2",
            "details": [...]
          }
        ]
      }
    ],
    "pagination": {
      "current_page": 1,
      "page_size": 20,
      "total_count": 1,
      "total_pages": 1,
      "has_next": false,
      "has_prev": false
    }
  }
}
``` 