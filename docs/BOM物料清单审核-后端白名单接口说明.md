# BOM物料清单审核 / 保存 - 后端白名单接口说明

接口用途：前端在 `/scm/item/bom/product-bom` 对 `BR SO BOM List` 主表及 `BR SO BOM List Details` 子表做 **仅保存** 或 **审核保存**：均按「销售订单号 + 成品」定位单据，对明细执行行级 Upsert（更新已有行 + 新增行）。

> **前端必读：** 已有明细行提交时请 **务必携带子表 `name`**（或与 `name` 相同的 **`id`**），否则易因业务键匹配失败而 **重复插入明细**。含义与对接约定见：[BOM审核-子表明细name字段前端说明.md](./BOM审核-子表明细name字段前端说明.md)。

---

## 0. 白名单方法怎么选（给前端）

| 场景 | 调用方法 | POST 路径 | 与 `status` 的关系（见 §0.1） |
|------|-----------|-----------|-------------------------------|
| 用户点「保存」/ 暂存 BOM，不完成审核 | `save_so_bom_list` | `/api/method/bairun_erp.utils.api.material.bom_item_list.save_so_bom_list` | 默认写入 **`saved`** |
| 用户点「审核」/ 确认物料清单 | `audit_so_bom_list` | `/api/method/bairun_erp.utils.api.material.bom_item_list.audit_so_bom_list` | 默认 **`approved`**（`mark_approved=1` 时） |
| 只改主表状态、不改明细/费率（如驳回、改草稿） | `update_so_bom_list_status` | `/api/method/bairun_erp.utils.api.material.bom_item_list.update_so_bom_list_status` | 传入的 `status` 原样写入（见 §1c） |

保存 / 审核 两个方法 **请求体结构相同**（`sales_order_no`、`item_code`、`header`、`details` 等）；**状态专线** `update_so_bom_list_status` 只需订单号、成品、`status` 等，见 §1c。

### 0.1 主表 `status` 约定（用于界面展示）

后端字段 `BR SO BOM List.status` 为 **Data**，没有固定枚举；下列为 **本仓库里实际会出现的常用值**（可按需扩展，也可用 `update_so_bom_list_status` 写入业务自定义值）：

| 后端值 `status` | 建议前端含义 | 常见来源 |
|------------------|-------------|----------|
| `draft` | 草稿 / 待处理 | 销售订单保存触发 BOM **同步**（`sales_order_bom_sync`）时，若上游未带状态则默认为 `draft` |
| `saved` | 已保存（未审核） | **`save_so_bom_list`** 且未传 `header.status` |
| `approved` | 已审核 | **`audit_so_bom_list`** 且 `mark_approved=1`（默认），或 **`update_so_bom_list_status`** 显式改为 `approved` |
| `po_raised` | 已生单（一键升单后） | 采购 **`save_purchase_orders` / `save_purchase_order`** 回写子表后，当本次一键生单命中该主表并成功回写明细采购单号后写入（常量 **`SO_BOM_LIST_STATUS_PO_RAISED`**，见 `bom_item_list.py`） |
| 其它字符串 | 自定义 | `header.status`、`update_so_bom_list_status`、同步数据里带的 `status` 等 |

**注意：** 从「已审核」再 **保存** 一次且 **不传** `header.status` 时，`status` 会变为 `saved`，表示当前草稿已保存、**需重新审核** 才能再视为已通过审核（与后端默认保存逻辑一致；若业务不希望降级，请与后端另议规则）。

---

## 1. 审核接口信息

- 方法路径：`bairun_erp.utils.api.material.bom_item_list.audit_so_bom_list`
- 调用方式：`POST /api/method/bairun_erp.utils.api.material.bom_item_list.audit_so_bom_list`
- 鉴权：登录用户（`allow_guest=False`）

### 1a. 审核成功后销售订单自动 Submit（ERPNext `docstatus`）

仅在 **`audit_so_bom_list`** 成功保存并 **commit** 当前这张 `BR SO BOM List` 之后，后端会尝试对同名 **`Sales Order`**（`Sales Order.name` = 本单 `order_no`）执行 **`submit()`**（草稿 `docstatus=0` → 已提交 `1`）。**前提**同时满足：

1. 销售订单存在，且当前为草稿（`docstatus=0`）；已提交、已取消的单据不会再次提交。
2. 该销售订单 **明细中去重后的每个非空 `item_code`**，在系统中均存在 **`BR SO BOM List`**（name = `{order_no}-{item_code}`），且主表 **`status` 均为 `approved`**（与全部成品 BOM 均已审核通过一致）。
3. 当前用户对该销售订单具备 **Submit** 权限。

若任一成品尚未审核通过、或缺少对应 BOM 清单：**不提交**（`data.sales_order_submit` = `skipped`）。若尝试 `submit()` 抛错：**本次 BOM 审核结果仍会保留**（已单独 `commit`），仅销售订单保持草稿；前端可根据 `failed` 与 `sales_order_message` 提示用户。

**返回体 `data` 附加字段**（`audit_so_bom_list` 成功时）：

| `sales_order_submit` | 含义 |
|----------------------|------|
| `submitted` | 已成功提交销售订单 |
| `already_submitted` | 销售订单此前已是已提交状态 |
| `skipped` | 未执行提交（条件不满足或未找到单等，见 `sales_order_message`） |
| `failed` | 执行提交失败（见 `sales_order_message`） |

`sales_order_message`：简短中文说明。**`save_so_bom_list`** 与 **`update_so_bom_list_status`** 不会触发上述逻辑。

---

## 1b. 保存接口信息（仅保存，不审核）

- 方法路径：`bairun_erp.utils.api.material.bom_item_list.save_so_bom_list`
- 调用方式：`POST /api/method/bairun_erp.utils.api.material.bom_item_list.save_so_bom_list`
- 鉴权：登录用户（`allow_guest=False`）
- 与审核接口差异：
  - **不写** 默认审核人 / 审核时间；忽略 `mark_approved`、`header.approved_by`、`header.approved_on`
  - **未传** `header.status` 时，成功后主表 `status` **固定为 `saved`**，便于与 `approved` 区分
  - 若传了 `header.status`（非空），则 **以传入为准**，不会强制改为 `saved`

---

## 1c. 仅更新主表状态 `update_so_bom_list_status`

- 方法路径：`bairun_erp.utils.api.material.bom_item_list.update_so_bom_list_status`
- 调用方式：`POST /api/method/bairun_erp.utils.api.material.bom_item_list.update_so_bom_list_status`
- 用途：**只改** 主表 `status`（及可选清空审核元数据），**不** 改 `details`、费率等；适合「驳回、改回草稿、强制标记状态」等，与保存/审核主流程分离。
- 顶层参数（`json_data` 或直接字段）：
  - `sales_order_no` / `order_no`、`item_code`：**必填**（定位单据，规则同保存/审核）
  - `status` / `audit_status` / `auditStatus`：**必填**，非空字符串，trim 后写入 `doc.status`
  - `clear_approval_meta` / `clearApprovalMeta`：**可选**，默认 `1`。为 `1` 且新 `status` **不等于** `approved` 时，清空 `approved_by`、`approved_on`，避免出现「状态已不是已审核却仍带审核人」。为 `approved` 时 **不会** 自动补审核人/时间（完整审核请走 `audit_so_bom_list`）。
- 返回结构与保存/审核成功时一致（`success`、`message`、`data`，`data` 含最新 `status`、`approved_by` 等）。

**示例：**

```json
{
  "json_data": {
    "sales_order_no": "SAL-ORD-2026-00003",
    "item_code": "配件_mm4io3o2ua3k",
    "status": "draft",
    "clear_approval_meta": 1
  }
}
```

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
- `status`（可选；**审核**且 `mark_approved=1` 时会被置为 `approved`；**保存**接口未传本字段时会由后端置为 `saved`）
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
      "details_count": 12,
      "sales_order_submit": "submitted",
      "sales_order_message": "销售订单已提交"
    }
  }
}
```

（`sales_order_submit` / `sales_order_message` 仅 **`audit_so_bom_list`** 返回；取值见 §1a。）

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

- **保存**与**审核**请分别调用 `save_so_bom_list` 与 `audit_so_bom_list`；**仅改状态** 用 `update_so_bom_list_status`。列表/详情页可用返回体 `data.status`（及上述 §0.1）展示「草稿 / 已保存 / 已审核」等。
- **`audit_so_bom_list`** 成功后查阅 `data.sales_order_submit`、`data.sales_order_message`（§1a），以确认销售订单是否已随「全部 BOM 已审核」自动 Submit；`failed` 时 BOM 已成功、需单独处理 SO。
- `get_product_bom_list_new` 的 `header.status` 已按 BR 主表实时值返回（优先 `BR SO BOM List.status`），与 `GET /api/resource/BR SO BOM List/{name}` 保持一致。
- 审核弹窗确认前，先在前端完成毛利相关重算，再将重算后的 `header` 一并提交。
- **编辑已有行时必须携带 `details[i].name`（或 `details[i].id`）**，详见 [BOM审核-子表明细name字段前端说明.md](./BOM审核-子表明细name字段前端说明.md)。
- 新增行建议显式给 `rowNo`，不传也可由后端自动分配。
- 如果需要“删除某些旧行”，请在后端新增独立删除策略接口；当前接口不会删除旧行。
