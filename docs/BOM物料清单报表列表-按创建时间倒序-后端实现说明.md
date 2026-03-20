# BOM 物料清单报表列表 — 按创建时间倒序（后端实现）

对应白名单方法：`bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report`。

## 排序（`order_by`）

- **默认**：`creation desc, name desc`（新建在前；`name` 二级排序保证分页稳定）。
- 请求体可传 **`order_by`**（与 `json_data` 内其它字段并列），格式为 **`字段名` + 空格 + `asc`/`desc`**；仅允许主表 **BR SO BOM List** 下列字段（否则回退默认）：
  - `creation`、`modified`、`delivery_date`、`order_no`、`item_code`、`name`、`status`、`customer_code`、`customer_name`
- 当主排序字段不是 `name` 时，实现会自动追加 `, name desc`。

与 **[BOM物料清单报表列表-后端白名单接口说明.md](./BOM物料清单报表列表-后端白名单接口说明.md)** 中参数表一致。

## 响应字段 `creation`

- 每条 `items[]` 含 **`creation`**：主表文档创建时间字符串（与 Frappe 一致，含微秒时常见形如 `YYYY-MM-DD HH:MM:SS.ffffff`）。
- 用于前端默认展示与单页内兜底排序；列表顺序以服务端 **`order_by`** 为准。

## 代码位置

- 实现：`bairun_erp/utils/api/sales/sales_order_query_bom_details.py`（`_sanitize_bom_report_order_by`、`_format_bom_report_creation`、`_row_to_bom_report_item`、`list_bom_material_report`）
- 单测：`bairun_erp/utils/api/sales/test_list_bom_material_report.py`
