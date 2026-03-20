# BOM 物料清单报表列表 — 后端白名单接口说明

面向 **Next.js / FastAPI** 与前端工程师：列表页 `/scm/item/bom/product-bom` 的真实数据源。明细页使用 `get_product_bom_list_new`（按 `sales_order_name` / 可选 `item_code`，读 BR SO BOM List 落库）。

**ERP 实现（排序、`order_by`、`creation` 回传）**：见 **[BOM物料清单报表列表-按创建时间倒序-后端实现说明.md](./BOM物料清单报表列表-按创建时间倒序-后端实现说明.md)**。

---

## 一、接口概览

| 项目 | 说明 |
|------|------|
| **方法名** | `list_bom_material_report` |
| **完整路径** | `bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report` |
| **ERPNext URL** | `POST /api/method/bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report` |
| **认证** | 需登录（`allow_guest=False`） |
| **数据源** | DocType **BR SO BOM List** 主表（一行 = 销售订单号 + 成品 `item_code`），与 `BR SO BOM List Details` 子表无关 |

### 与明细接口的关系

| 接口 | 用途 |
|------|------|
| **本接口** | 报表首页分页列表（索引行） |
| `get_product_bom_list_new` | 单张销售订单下物料清单明细（header + items + 纸箱/包材等），与列表同源落库 |
| `get_product_bom_list` | （旧）实时 BOM 展开，前端可不再调用 |

---

## 二、业务约定（联调前请与业务确认）

| 项目 | 当前实现 |
|------|----------|
| 列表行粒度 | 一行 = **一条 BR SO BOM List**（订单 + 成品），与同步写入主表一致 |
| **日期筛选字段** | 主表 **`delivery_date`（交货日期）**，闭区间 `[date_from, date_to]`；**无交货日期的记录不会出现在结果中** |
| `bomStatus` 展示 | 库内 `draft` / `未审核` → 展示「未审核」；`approved` / `submitted` / `已审核` → 「已审核」；其它原样返回 |
| 权限 | 当前用户需对 **BR SO BOM List** 具备 **读** 权限（Frappe 标准权限） |

---

## 三、请求参数

参数可 **直接作为 POST 表单字段** 提交，也可放在 **`json_data`** 中（与其它销售白名单一致，便于 FastAPI `POST /erpnext/resource` 统一传 JSON）。

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `date_from` | string | **是** | — | `YYYY-MM-DD` |
| `date_to` | string | **是** | — | `YYYY-MM-DD`，与 `date_from` 同为闭区间 |
| `page_number` | int | 否 | 1 | 从 1 开始 |
| `page_size` | int | 否 | 20 | 最大 **100**，超出按 100 处理 |
| `sales_order_name` | string | 否 | — | 精确匹配主表 `order_no`（销售订单号） |
| `customer` | string | 否 | — | 精确匹配 `customer_code`（单位编号） |
| `customer_name` | string | 否 | — | `customer_name` 模糊匹配 |
| `bom_status` | string | 否 | — | 可传 `未审核` / `已审核`，或 `draft` / `approved` / `submitted`，或与库内 `status` 完全一致 |
| `item_code` | string | 否 | — | 存货编码 **模糊** 匹配 |
| `order_by` | string | 否 | `creation desc` | 列表排序；默认主表按 **`creation` 降序**（实现为 `creation desc, name desc` 稳定分页）；仅允许白名单字段，见 **[按创建时间倒序说明](./BOM物料清单报表列表-按创建时间倒序-后端实现说明.md)** |

**示例（推荐：`json_data` 一包到底）**

```json
{
  "date_from": "2026-01-01",
  "date_to": "2026-12-31",
  "page_number": 1,
  "page_size": 20,
  "sales_order_name": "",
  "customer": "",
  "customer_name": "",
  "bom_status": "未审核",
  "item_code": "",
  "order_by": "creation desc"
}
```

（经 FastAPI 时：上述对象放在请求体字段 `json_data` 内。）

若你们网关把整段 body 当作方法参数解析，也可扁平传同级字段（无 `json_data` 包裹），效果相同。

---

## 四、响应格式

### 成功

```json
{
  "success": true,
  "message": null,
  "data": {
    "page_number": 1,
    "page_size": 20,
    "total_count": 128,
    "total_pages": 7,
    "items": [
      {
        "id": "WZ0002603001-0305061",
        "bomStatus": "未审核",
        "salesOrderNo": "WZ0002603001",
        "unitCode": "CUST-175",
        "unitName": "某某客户",
        "itemCode": "0305061",
        "itemName": "26-109黑色顶片+金色外盖（成品）",
        "deliveryDate": "2026-03-15",
        "materialAuditor": "",
        "materialAuditDate": "",
        "documentCreator": "陈艳群",
        "creation": "2026-03-10 14:22:33.000000"
      }
    ]
  }
}
```

| 字段 | 说明 |
|------|------|
| `id` | 稳定主键，当前为主表 `name`（格式 `{order_no}-{item_code}`） |
| `bomStatus` | 审核状态展示文案 |
| `salesOrderNo` | 与明细页查询参数 `sales_order_name` 一致 |
| `unitCode` / `unitName` | 单位编号 / 全名，来自主表 `customer_code` / `customer_name` |
| `itemCode` / `itemName` | 成品存货编码 / 名称 |
| `deliveryDate` | `YYYY-MM-DD` |
| `materialAuditor` / `materialAuditDate` | 主表 `approved_by` / `approved_on`（日期取日部分） |
| `documentCreator` | 主表 `created_by` |
| `creation` | 主表 **创建时间**（Frappe `creation`），用于排序与展示；服务端列表顺序以 `order_by` 为准 |

**行号 `rowNo`**：由前端在**当前排序**（默认按 `creation` 倒序）下按 `(page_number - 1) * page_size + index + 1` 重算，后端可不返回。

### 失败

```json
{
  "success": false,
  "message": "错误原因（如日期格式非法、无权限等）"
}
```

---

## 五、经 FastAPI 调用（与现网一致）

与 `get_sales_order_details_list`、`fetchProductBomList` 相同：Next.js Server Action → **`getErpnextResource`** → **`POST /erpnext/resource`**。

- 将 ERP 方法路径设为：  
  `bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report`
- `json_data` 中放入第三节参数表中的字段（建议含 `date_from`、`date_to` 及分页）。

**注意**：Frappe 返回外层可能还有 `message` 包装（如 `data.message`），与现有 BOM 接口解析方式保持一致即可。

---

## 六、本地 / 服务器调试

```bash
bench --site site2.local execute \
  bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report \
  --kwargs '{"json_data": {"date_from": "2026-01-01", "date_to": "2026-12-31", "page_number": 1, "page_size": 20}}'
```

单元测试：

```bash
bench --site site2.local run-tests \
  --module bairun_erp.utils.api.sales.test_list_bom_material_report
```

---

## 七、相关代码与文档

| 说明 | 路径 |
|------|------|
| 白名单实现 | `bairun_erp/utils/api/sales/sales_order_query_bom_details.py` → `list_bom_material_report` |
| 排序与 `creation` | [BOM物料清单报表列表-按创建时间倒序-后端实现说明.md](./BOM物料清单报表列表-按创建时间倒序-后端实现说明.md) |
| 主表/字段说明 | `bairun_erp/doctype/br_so_bom_list/README.md` |
| 明细接口 | 同文件 `get_product_bom_list_new`（及旧方法 `get_product_bom_list` 对照） |

---

*若后续改为按销售订单 `transaction_date` 筛列表，需在 ERP 侧增加主表字段或改查询逻辑，并同步更新本文「日期筛选字段」。*
