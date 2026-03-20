# BOM一键生单后端改造完成说明（前后端联调）

本文用于通知前端：`save_purchase_orders` 与 `get_product_bom_list_new` 已按联调需求完成增强，并保持兼容旧字段。

---

## 1. 改造结论

已完成以下改造：

- 一键生单接口（`save_purchase_orders`）支持按行回执 `line_results`。
- BOM 明细查询接口（`get_product_bom_list_new`）可回读 `order_status`、`purchase_order_no`，并新增 `purchase_order_nos`。
- 保留原有 camelCase 字段，兼容现有前端逻辑。

---

## 2. 接口一：一键生单（创建接口）

- 方法路径：`bairun_erp.utils.api.buying.purchase_order_add.save_purchase_orders`
- 调用方式：`POST /api/method/bairun_erp.utils.api.buying.purchase_order_add.save_purchase_orders`
- 请求参数：保持不变（`order_data_list`）

### 2.1 新增按行回执字段

`data.line_results`（按提交顺序对应）每行包含：

- `line_no`：行号（从 1 开始）
- `item_code`：该行主物料编码（从该行 `items[0].item_code` 提取）
- `success`：是否成功
- `purchase_order_no`：采购订单号（成功时返回）
- `order_status`：生单状态（当前规则：成功=`已生单`，失败=`未生单`）
- `message`：失败原因（失败时返回）

### 2.2 成功响应示例

```json
{
  "message": {
    "data": {
      "success": true,
      "count": 2,
      "names": ["PUR-ORD-2026-00001", "PUR-ORD-2026-00002"],
      "line_results": [
        {
          "line_no": 1,
          "item_code": "RM-001",
          "success": true,
          "purchase_order_no": "PUR-ORD-2026-00001",
          "order_status": "已生单",
          "message": ""
        },
        {
          "line_no": 2,
          "item_code": "RM-002",
          "success": true,
          "purchase_order_no": "PUR-ORD-2026-00002",
          "order_status": "已生单",
          "message": ""
        }
      ],
      "message": "已批量保存并提交 2 张采购订单"
    }
  }
}
```

### 2.3 失败响应示例（含定位）

```json
{
  "message": {
    "success": false,
    "error": "采购明细第 1 行物料编码不能为空",
    "index": 0,
    "data": {
      "count": 0,
      "line_results": [
        {
          "line_no": 1,
          "item_code": "",
          "success": false,
          "purchase_order_no": null,
          "order_status": "未生单",
          "message": "采购明细第 1 行物料编码不能为空"
        }
      ]
    }
  }
}
```

---

## 3. 接口二：BOM 明细查询（读接口）

- 方法路径：`bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list_new`
- 调用方式：`POST /api/method/bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list_new`
- 返回结构：保持原有 `header/items/cartonItems/packagingItems`

### 3.1 每行补齐字段（已支持）

在 `items` / `cartonItems` / `packagingItems` 的行对象中，现同时返回：

- `orderStatus`（兼容旧前端）
- `purchaseOrderNo`（兼容旧前端）
- `order_status`（新增，snake_case）
- `purchase_order_no`（新增，snake_case）
- `purchase_order_nos`（新增，数组，多单场景）

说明：

- 若历史数据 `order_status` 为空，后端兜底规则：
  - 有 `purchase_order_no` -> `已生单`
  - 无 `purchase_order_no` -> `未生单`

---

## 4. 前端接入建议

- 一键生单后，优先使用 `data.line_results` 回填表格行状态与单号，不再依赖前端推断。
- BOM 明细回读时，优先读取 snake_case 字段：
  - `order_status`
  - `purchase_order_no`
  - `purchase_order_nos`
- 若旧页面仍在使用 camelCase，可继续使用 `orderStatus` / `purchaseOrderNo`，不影响。

### 4.1 生单后回写 BR SO BOM List Details（库内持久化）

自本说明更新起：`save_purchase_order` / `save_purchase_orders` 在 **采购单保存并提交成功后**，会按规则回写 **`BR SO BOM List Details`** 上的 **`purchase_order_no`**（支持与已有值逗号拼接去重）与 **`order_status` = `已生单`**，以便 `get_product_bom_list_new` 全量重拉与界面一致。

**建议前端传入（精度从高到低）**

| 位置 | 字段 | 说明 |
|------|------|------|
| `items[]` 每行 | `br_so_bom_list_detail_name` | 子表行 `name`（如 Desk 里明细行的 name），与 BOM 行一一对应时最可靠 |
| 同上 | `bom_list_detail_name` | 与上一项同义 |
| `items[]` 每行 | `bom_code` / `bomCode` | 当同一 `parent + item_code` 多行时，与明细 `bom_code` 联合匹配 |
| `order_data` 主表 | `bom_finished_item_code` / `finished_item_code` | 可选；用于指定 BR SO BOM List 主表名中的 **成品 `item_code`**（当 `sales_order_item` 对应行不是成品行、自动解析会错时） |

**后端自动匹配（未传子表行 name 时）**：根据 `sales_order` + `sales_order_item` 解析成品编码 → 主表 `"{sales_order}-{成品编码}"` → 在子表中按 `item_code`、优先 `supplier_code`（与 PO 供应商一致）、可选 `bom_code` 定位行并回写。

---

## 5. 当前口径说明（重要）

本次改造已满足“按行回执 + 可回读展示”。  
“部分生单（按需求数量覆盖率）”与“强幂等去重（业务键防重复建单）”属于下一阶段业务规则增强，当前未在本次改造内引入强约束。

