# BOM物料清单审核 - 后端白名单接口说明

接口用途：前端在 `/scm/item/bom/product-bom` 执行“审核”时，按「销售订单号 + 成品」更新 `BR SO BOM List` 主表，并对 `BR SO BOM List Details` 明细执行行级 Upsert（更新已有行 + 新增行）。

> **前端必读：** 已有明细行审核时请 **务必携带子表 `name`**（或与 `name` 相同的 **`id`**），否则易因业务键匹配失败而 **重复插入明细**。含义与对接约定见：[BOM审核-子表明细name字段前端说明.md](./BOM审核-子表明细name字段前端说明.md)。

---

## 1. 接口信息

- 方法路径：`bairun_erp.utils.api.material.bom_item_list.audit_so_bom_list`
- 调用方式：`POST /api/method/bairun_erp.utils.api.material.bom_item_list.audit_so_bom_list`
- 鉴权：登录用户（`allow_guest=False`）

---

## 2. 请求参数

支持两种传参：

1) 直接扁平参数；  
2) `json_data`（推荐，支持对象或 JSON 字符串）。

### 2.1 顶层参数

- `sales_order_no` / `order_no` / `salesOrderNo` / `orderNo`：销售单号（必填）
- `item_code` / `itemCode`：成品编码（必填）
- `header`：主表审核字段（可选）
- `details`：明细数组（可选，默认 `[]`）
- `mark_approved` / `markApproved`：是否自动置已审核（可选，默认 `1`）

### 2.2 `header` 可传字段

- `running_cost_rate` / `runningCostRate`
- `transport_fee_rate` / `transportFeeRate`
- `tax_rate` / `taxRate`
- `gross_margin` / `grossMargin`
- `status`（可选；当 `mark_approved=1` 时会被覆盖为 `approved`）
- `approved_by` / `approvedBy`
- `approved_on` / `approvedOn`

### 2.3 `details` 行字段（camel/snake 都支持）

常用字段：

- `name` / `id`（**已有行强烈建议必填**；`name` 为子表主键，`id` 与查询接口 `get_product_bom_list_new` 返回的 `id` 同义，后端会统一按子表 `name` 匹配）
- `row_no` / `rowNo`
- `item_code` / `itemCode`（必填）
- `level`
- `bom_code` / `bomCode`
- `item_name` / `itemName`
- `required_qty_override` / `requiredQtyOverride`
- `supplier_code` / `supplierCode`
- `supplier_name` / `supplierName`
- `process_name` / `processName` / `process`
- `estimated_cost` / `estimatedCost`
- `order_cost` / `orderCost`

---

## 3. 明细 Upsert 规则

**前端约定（与业务一致）：**

- **有 `name` / `id`（非空）**：表示**已有子表行**，本次只做**更新**。  
- **没有 `name` / `id`**：才表示**手工新增**一行；详见 [BOM审核-子表明细name字段前端说明.md](./BOM审核-子表明细name字段前端说明.md)。

每行明细按以下优先级匹配更新目标：

1. 传 `name`（或 `id`）且命中已有子表行：执行更新；  
2. 未传：按业务键 `(row_no, item_code, bom_code, level)` 尝试命中更新；  
3. 未命中：插入新行（新增场景；也可能是已有行漏传 `name` 导致的误新增）。

说明：

- 该接口不删除未传入的旧行（仅“更新 + 新增”）。
- `row_no` 为空的新行会自动顺延分配。

---

## 4. 请求示例

```json
{
  "json_data": {
    "sales_order_no": "SAL-ORD-2026-00003",
    "item_code": "配件_mm4io3o2ua3k",
    "header": {
      "runningCostRate": 6.5,
      "transportFeeRate": 3.2,
      "taxRate": 0.13,
      "grossMargin": 22.15
    },
    "details": [
      {
        "name": "v2x3l5j8m9",
        "rowNo": 1,
        "itemCode": "RM-001",
        "supplierName": "江苏某供应商",
        "process": "冲压"
      },
      {
        "rowNo": 11,
        "itemCode": "RM-NEW-01",
        "level": 2,
        "bomCode": "A1-4",
        "itemName": "新增拆分料",
        "ratioQty": 0.2,
        "supplierCode": "SUP-009"
      }
    ],
    "mark_approved": 1
  }
}
```

---

## 5. 返回示例

成功：

```json
{
  "message": {
    "success": true,
    "message": "审核保存成功",
    "data": {
      "name": "SAL-ORD-2026-00003-配件_mm4io3o2ua3k",
      "order_no": "SAL-ORD-2026-00003",
      "item_code": "配件_mm4io3o2ua3k",
      "status": "approved",
      "approved_by": "test@example.com",
      "approved_on": "2026-03-20 15:40:11.123456",
      "details_count": 12
    }
  }
}
```

失败（示例）：

```json
{
  "message": {
    "success": false,
    "message": "未找到对应 BOM 清单: SAL-ORD-2026-00003-配件_mm4io3o2ua3k"
  }
}
```

---

## 6. 前端对接建议

- 审核弹窗确认前，先在前端完成毛利相关重算，再将重算后的 `header` 一并提交。
- **编辑已有行时必须携带 `details[i].name`（或 `details[i].id`）**，详见 [BOM审核-子表明细name字段前端说明.md](./BOM审核-子表明细name字段前端说明.md)。
- 新增行建议显式给 `rowNo`，不传也可由后端自动分配。
- 如果需要“删除某些旧行”，请在后端新增独立删除策略接口；当前接口不会删除旧行。
