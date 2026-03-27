# BOM 物料清单 DocType 说明（BR SO BOM List / BR SO BOM List Details）

本文档面向前端/对接工程师，说明 **BR SO BOM List**（BOM 概览主表）与 **BR SO BOM List Details**（BOM 明细子表）的字段含义，以及如何通过 **Frappe REST API / frappe.client** 对这两张表进行增删改查。  
两张表表达同一业务：按「销售订单 + 成品」维度的 BOM 概览，以及其下多行物料明细。

---

## 一、表关系与命名规则

| 逻辑         | DocType 名称             | 说明 |
| ------------ | ------------------------ | ---- |
| BOM 概览主表 | **BR SO BOM List**       | 一条主表记录 = 一张 BOM 单（订单号 + 成品） |
| BOM 明细子表 | **BR SO BOM List Details**| 子表多行通过 `details` 挂在同一主表下，不可单独存在 |

- **主表文档名（name）**：按 `{order_no}-{item_code}` 自动生成，例如 `WZ0002603001-0305061`。
- 对外接口统一使用 **REST API**，后端仅暴露**白名单方法**，前端通过 `frappe.client` 或直接调用 REST 进行读写。

### 报表列表页（只读聚合）

BOM 物料清单报表**列表页**（多行分页、按交货日期等筛选）请使用白名单方法 **`list_bom_material_report`**（数据源即本主表，不返回子表 `details`）。实现路径：`bairun_erp.utils.api.sales.sales_order_query_bom_details.list_bom_material_report`。  
**对接说明文档**：仓库内 `docs/BOM物料清单报表列表-后端白名单接口说明.md`。

**明细页（与实时 BOM 展开同结构）**：若要以主从表为数据源、返回与 `get_product_bom_list` 相同的 `header` / `items` / `cartonItems` / `packagingItems`，请调用白名单方法 **`get_product_bom_list_new`**（`bairun_erp.utils.api.sales.sales_order_query_bom_details.get_product_bom_list_new`）。需已存在由销售订单保存同步生成的 BR SO BOM List 记录。

**仅保存（主表更新 + 明细 Upsert，`status` 默认 `saved`）**：**`save_so_bom_list`**。**审核保存**：**`audit_so_bom_list`**。**仅更新主表 `status`**：**`update_so_bom_list_status`**。路径均在 `bairun_erp.utils.api.material.bom_item_list`。对接文档：`docs/BOM物料清单审核-后端白名单接口说明.md`。

---

## 二、主表 BR SO BOM List 字段说明

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `order_no` | Data | 销售订单号，如 WZ0002603001 |
| `status` | Data | 业务状态：`saved` / `approved` / **`po_raised`**（已生单，明细全部已关联采购单号）等，详见对接文档 §0.1 |
| `customer_code` | Data | 单位编号（客户编号），如 175、060、062 |
| `customer_name` | Data | 单位全名（客户全名） |
| `item_code` | Data | 存货编码（成品），如 0305061、0101403 |
| `item_name` | Data | 存货全名（成品名称） |
| `delivery_date` | Date | 交货日期，如 2026-03-15 |
| `approved_by` | Data | 物料审核人 |
| `approved_on` | Datetime | 物料审核日期 |
| `created_by` | Data | 单据制单人 |
| `project_no` | Data | 项目号 |
| `order_qty` | Float | 订单数量 |
| `sales_price` | Currency | 销售单价 |
| `unit_estimated_cost` | Currency | 单件产品成本（元/件） |
| `running_cost_rate` | Percent | 运行成本比例（占销售金额 %） |
| `transport_fee_rate` | Percent | 运输成本比例（占销售金额 %） |
| `tax_rate` | Float | 税点（如 0.13 表示 13%） |
| `gross_margin` | Percent | 毛利率 |
| `warehouse_code` | Data | 成品仓库编码 |
| `warehouse_name` | Data | 成品仓库全名 |
| `inventory_qty` | Float | 成品在对应仓库的库存数量（可空） |
| `details` | Table | 子表，options = **BR SO BOM List Details**，多行物料明细 |

*说明：货款金额 = order_qty × sales_price，可不存库，查询时计算即可。*

---

## 三、子表 BR SO BOM List Details 字段说明

子表为**表格子表**，仅能随主表一起保存，不能单独插入/更新。每条子表行对应一行物料。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `row_no` | Int | 行号 |
| `item_code` | Data | 存货编码 |
| `level` | Int | BOM 层级，1=一级、2=二级…（可空） |
| `bom_code` | Data | BOM 编码，如 A1-1、A1-1-1（可空） |
| `item_name` | Data | 物料名称（存货全名） |
| `item_group` | Data | 物料组，如成品、半成品、毛坯、纸箱等（可空） |
| `ratio_qty` | Float | 配比：每单位成品所需该物料数量 |
| `required_qty_override` | Float | 需求数量覆盖值（领用等场景，可空） |
| `inventory_qty` | Float | 库存数量（可空） |
| `supplier_code` | Data | 供应商编号 |
| `supplier_name` | Data | 供应商全名 |
| `process_name` | Data | 工艺（注意：接口/DB 字段名为 process_name，与系统保留字 process 区分） |
| `estimated_cost` | Currency | 单价（单位预估成本，可空） |
| `order_cost` | Currency | 金额（该物料在本单下的总成本） |
| `warehouse_code` | Data | 仓库编号 |
| `warehouse_name` | Data | 仓库全名 |
| `warehouse_slot` | Data | 库位（可空） |
| `order_status` | Data | 生单状态，如 未生单 |
| `order_confirmation_status` | Data | 订单确认状态：空 / 订单已确认 / 毛坯已收 / 在制（可空） |
| `received_qty` | Float | 到库数量（可空） |
| `unreceived_qty` | Float | 未到库数量（可空） |
| `loss_ratio` | Float | 损耗比（可空） |
| `purchase_order_no` | Data | 采购订单编号（可空） |

---

## 四、数据写入与增删改查（REST API / frappe.client）

对外通过 **REST API** 调用，后端仅开放**白名单**接口。前端可使用 **frappe.client** 或直接 HTTP 调用同一套 API。

### 4.1 保存整单（主表 + 子表）——推荐

数据写入这两张表时，**应使用主表 + 子表一起保存**，与业务上「一张 BOM 单 + 多行物料」一致：

- **frappe.client.insert**：新建一条主表记录，并同时写入其 `details` 子表多行。
- **frappe.client.save** / **frappe.client.set_value** 等：在已有主表文档上更新，并更新子表（通过传完整 `details` 数组）。

示例（概念，具体以你们前端封装的 client 为准）：

```javascript
// 新建：主表 + 子表一次写入
frappe.client.insert({
  doc: {
    doctype: "BR SO BOM List",
    order_no: "WZ0002603001",
    item_code: "0305061",
    customer_code: "175",
    customer_name: "某客户",
    delivery_date: "2026-03-15",
    order_qty: 100,
    sales_price: 50.00,
    details: [
      {
        doctype: "BR SO BOM List Details",
        row_no: 1,
        item_code: "MAT001",
        item_name: "物料A",
        ratio_qty: 2,
        order_cost: 20.00
      },
      {
        doctype: "BR SO BOM List Details",
        row_no: 2,
        item_code: "MAT002",
        item_name: "物料B",
        ratio_qty: 0.5,
        order_cost: 10.00
      }
    ]
  }
}).then(doc => {
  // doc.name 即主表 name，如 "WZ0002603001-0305061"
});
```

- 更新整单：用 **frappe.client.save** 传入完整主表字段 + `details` 数组，会覆盖该主表下原有子表行并写回这两张表。

### 4.2 REST API 路径（标准白名单）

以下为 Frappe 标准 Resource API，需在后端对 **BR SO BOM List** 开放相应权限与白名单：

| 操作 | 方法 | 路径（示例） | 说明 |
|------|------|--------------|------|
| 列表 | GET | `/api/resource/BR SO BOM List` | 分页、过滤、排序 |
| 单条 | GET | `/api/resource/BR SO BOM List/{name}` | 含 `details` 子表 |
| 新建 | POST | `/api/resource/BR SO BOM List` | body 中带 `details` 数组 |
| 更新 | PUT | `/api/resource/BR SO BOM List/{name}` | body 中带完整 `details` |
| 删除 | DELETE | `/api/resource/BR SO BOM List/{name}` | 会级联删除其子表行 |

- 子表 **BR SO BOM List Details** 不单独提供 Resource 接口，所有写入都通过主表的 `details` 字段完成，保证主从一致。

### 4.3 前端常用 frappe.client 方法（白名单封装）

- **frappe.client.insert({ doc })** — 插入主表 + 子表，数据写入上述两张表。
- **frappe.client.get(doc)** — 获取单条主表文档（含 `details`）。
- **frappe.client.get_list(doctype, filters, fields, …)** — 列表查询主表。
- **frappe.client.save(doc)** — 保存（新建或更新）主表并写回 `details` 到子表。
- **frappe.client.delete(doctype, name)** — 删除主表文档（子表行会一并删除）。

以上均为 Frappe 标准白名单方式，对外通过 REST 暴露即可。

---

## 五、小结

- **BR SO BOM List**：BOM 概览主表；**BR SO BOM List Details**：BOM 明细子表，两者表达同一业务，数据应一起写入。
- 字段含义见第二节（主表）、第三节（子表）；子表工艺字段接口/DB 名为 **process_name**。
- 保存、更新、删除均以**主表**为入口，通过 **frappe.client.insert / save / get / get_list / delete** 或对应 REST 接口操作，数据会正确写入这两张表；子表不单独对外接口。

如有新字段或新接口，以后端白名单与权限配置为准。
