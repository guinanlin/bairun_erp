# 原材料页面字段 ↔ Item 表（tabItem）匹配表

本文档对照 `item-list.md` 中的需求字段，分析 **仅从 Item 主表（tabItem）及关联** 能否获取到该字段。  
**采购相关字段** 在 Item 表中不存在，统一标注为「放空/0」；其余字段标明数据来源与取值方式。

---

## 映射关系总表（一表看清）

| 需求字段 | 类型 | 数据来源 | 对应字段/取值方式 | 返回值说明 |
|----------|------|----------|-------------------|-------------|
| `id` | string | Item 主表 | `name`（即 item_code） | 物料维度时用 item_code 作为 id |
| `date` | string | Item 主表 | `creation` 格式化为 `yyyy-MM-dd` | 物料创建日期 |
| `projectNo` | string | — | — | **放空** `""`（采购相关） |
| `itemFullName` | string | Item 主表 | `item_name` 或 `item_code + " - " + item_name` | 物料名称/全名 |
| `orderQty` | number | — | — | **放 0**（采购相关） |
| `unitPrice` | number | — | — | **放 0**（采购相关） |
| `receivedQty` | number | — | — | **放 0**（采购相关） |
| `unreceivedQty` | number | — | — | **放 0**（采购相关） |
| `inStockQty` | number | Bin 表 | 按 `item_code`（+ 可选 `warehouse`）汇总 `actual_qty` | 在库数 |
| `supplierId` | string | — | — | **放空** `""`（采购相关） |
| `supplier` | string | — | — | **放空** `""`（采购相关） |
| `inventoryCost` | number | Item 主表 | `valuation_rate` | 库存成本 |
| `salesPrice` | number | Item 主表 | `standard_rate` | 销售定价 |
| `warehouse` | string | Item Default 子表 | `item_defaults.default_warehouse`（按公司取） | 默认仓库 |
| `warehouseLocation` | string | — | — | **放空** `""`（暂无库位字段） |
| `unit` | string | Item 主表 | `stock_uom` → UOM 的 name | 单位（件/箱/kg 等） |
| `workInstructionUrl` | string | — | — | **放空** `""`（暂无作业指导书字段） |
| `status` | string | — | — | **放空** `""`（采购业务状态，暂时放空） |

**弹窗字段（添加原材料物料）**

| 需求字段 | 类型 | 数据来源 | 对应字段/取值方式 | 返回值说明 |
|----------|------|----------|-------------------|-------------|
| `itemName` | string | Item 主表 | `item_name` | 物料名称 |
| `inventoryCost` | number | Item 主表 | `valuation_rate` | 库存成本，默认 0 |
| `salesPrice` | number | Item 主表 | `standard_rate` | 销售定价，默认 0 |

**参考数据 - 物料下拉**

| 需求字段 | 类型 | 数据来源 | 对应字段/取值方式 | 返回值说明 |
|----------|------|----------|-------------------|-------------|
| `id` | string | Item 主表 | `name`（item_code） | 物料唯一标识 |
| `fullName` | string | Item 主表 | `item_name` 或 `item_code + " - " + item_name` | 物料全名 |

---

## 一、原材料明细主表（主列表）字段匹配（明细）

| 需求字段 | 类型 | 说明 | Item 表是否有 | 对应 Item 字段 / 取值建议 |
|----------|------|------|----------------|---------------------------|
| `id` | string | 行记录唯一标识 | ❌ 不适用 | 行记录 ID 来自业务表（如采购订单明细），不是物料主数据。若接口只按「物料维度」返回，可用 `item_code`（即 Item.name）作为物料唯一标识。 |
| `date` | string | 单据日期 | ✅ 有 | Item 表有 **创建时间**：`creation`。格式化为 `yyyy-MM-dd` 即可。 |
| `projectNo` | string | 采购单号 | ❌ 无 | **采购相关**，Item 表无。放空。 |
| `itemFullName` | string | 物料名称（存货全名） | ✅ 可组合 | Item 有 `item_name`；无单独「全名」字段。可用 `item_name`，或 `item_code + " - " + item_name` 作为 fullName。 |
| `orderQty` | number | 采购订单数 | ❌ 无 | **采购相关**，Item 表无。放 0。 |
| `unitPrice` | number | 采购单价 | ❌ 无 | **采购相关**，Item 表无。放 0。 |
| `receivedQty` | number | 到库数 | ❌ 无 | **采购相关**，Item 表无。放 0。 |
| `unreceivedQty` | number | 未到库数 | ❌ 无 | **采购相关**，Item 表无。放 0。 |
| `inStockQty` | number | 在库数 | ✅ 可获取 | 按 **Bin** 表用 `item_code`（及可选 `warehouse`）汇总 `actual_qty` 即得在库数。Item 维度可汇总该物料在各仓库的库存。 |
| `supplierId` | string | 供应商 ID | ⚠️ 非主表 | 供应商在 **Item Supplier** 子表（supplier_items）或采购单中，Item 主表无默认供应商 ID。放空。 |
| `supplier` | string | 供应商名称 | ⚠️ 非主表 | 同上，在子表或采购单。Item 主表无。放空。 |
| `inventoryCost` | number | 库存成本 | ✅ 有 | Item 表字段：`valuation_rate`（计价/库存成本）。可直接映射。 |
| `salesPrice` | number | 销售定价 | ✅ 有 | Item 表字段：`standard_rate`（标准售价）。可直接映射。 |
| `warehouse` | string | 仓库 | ✅ 有 | 从 **Item Default** 子表（item_defaults）取 `default_warehouse`（按公司/上下文取默认仓库）。 |
| `warehouseLocation` | string | 库位 | ❌ 无 | 目前 Item 侧无库位字段。**先放空**。 |
| `unit` | string | 单位 | ✅ 有 | Item 表字段：`stock_uom`（默认库存单位，Link 到 UOM）。取 UOM 的 name 即可。 |
| `workInstructionUrl` | string | 作业指导书图片 URL | ❌ 无 | Item 表只有 `image`（商品图），无作业指导书字段。**先放空**，后期若有再接。 |
| `status` | string | 待入库/已入库/已出库 | ❌ 采购相关 | 属于采购/入库业务状态，**暂时放空**。 |

---

## 二、添加原材料物料弹窗字段匹配

| 需求字段 | 类型 | 说明 | Item 表是否有 | 对应 Item 字段 / 取值建议 |
|----------|------|------|----------------|---------------------------|
| `itemName` | string | 物料名称 | ✅ 有 | Item 表字段：`item_name`。 |
| `inventoryCost` | number | 库存成本，默认 0 | ✅ 有 | Item 表字段：`valuation_rate`。 |
| `salesPrice` | number | 销售定价，默认 0 | ✅ 有 | Item 表字段：`standard_rate`。 |

弹窗这三个字段 **全部可以从 Item 表直接或默认取值**。

---

## 三、参考数据 - 物料（Item）下拉

| 需求字段 | 类型 | 说明 | Item 表是否有 | 对应 Item 字段 / 取值建议 |
|----------|------|------|----------------|---------------------------|
| `id` | string | 物料唯一标识 | ✅ 有 | Item 的 `name` 即 `item_code`，可作为 id。 |
| `fullName` | string | 物料名称（存货全名） | ✅ 可组合 | 用 `item_name`，或 `item_code + " - " + item_name`。 |

下拉用的物料列表 **完全可以从 Item 表读出**。

---

## 四、汇总：Item 表可直接提供的字段

从 **Item 主表（tabItem）** 能直接拿到的、与需求相关的字段如下：

| Item 表/关联 | 对应需求 | 备注 |
|-------------|----------|------|
| `name` / `item_code` | 物料唯一标识 id、参考数据 id | 主键 |
| `creation` | date（单据日期） | 创建时间，格式化为 yyyy-MM-dd |
| `item_name` | itemFullName、itemName、fullName | 物料名称 |
| Bin 表 `actual_qty` | inStockQty（在库数） | 按 item_code（+ warehouse）汇总 |
| Item Default 子表 `default_warehouse` | warehouse（仓库） | 按公司取默认仓库 |
| `stock_uom` | unit | 需解析 UOM 的 name（件/箱/kg 等） |
| `valuation_rate` | inventoryCost（库存成本） | 计价/库存成本 |
| `standard_rate` | salesPrice（销售定价） | 标准售价 |
| `image` | 仅能做「商品图」 | 无作业指导书时可考虑复用或留空 |

---

## 五、采购相关字段（统一放空/0）

以下字段与采购/入库/出库业务强相关，**Item 表内没有**，按你的要求统一放空或放零：

| 需求字段 | 建议返回值 |
|----------|------------|
| projectNo | 空字符串 `""` |
| orderQty | `0` |
| unitPrice | `0` |
| receivedQty | `0` |
| unreceivedQty | `0` |
| supplierId | 空字符串 `""` |
| supplier | 空字符串 `""` |
| status | 空字符串 `""`（属采购业务，暂时放空） |

---

## 六、仅需放空、暂不从其他表取的字段

| 需求字段 | 说明 |
|----------|------|
| warehouseLocation | 目前 Item 侧无库位字段，**先放空**。 |
| workInstructionUrl | 作业指导书无专用字段，**先放空**，后期若有再接。 |
| status | 属采购/入库状态，**暂时放空**。 |

其余原先写「需要其他表」的：**date** 已改为用 Item 的 `creation`，**inStockQty** 用 Bin 按 item 汇总，**warehouse** 用 Item Default 的 `default_warehouse`，均视为可从 Item 侧（或 Bin/Item Default）取得。

---

**结论**：  
- **Item 表及关联**可提供：物料标识、创建日期（date）、物料名称、单位、库存成本、销售定价、在库数（Bin）、默认仓库（Item Default）。  
- 采购相关（单号、订单数、单价、到库数、未到库数、供应商、status）统一放空/0。  
- warehouseLocation、workInstructionUrl 先放空。
