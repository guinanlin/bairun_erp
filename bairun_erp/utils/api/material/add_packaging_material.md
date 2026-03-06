# 添加包材（add_packaging_material）接口说明

本文档说明白名单方法 **添加包材** 的调用方式、参数、返回结果及异常。

---

## 一、方法信息

| 项目 | 说明 |
|------|------|
| **方法名** | `add_packaging_material` |
| **模块路径** | `bairun_erp.utils.api.material.item` |
| **白名单** | 是（`@frappe.whitelist()`） |
| **用途** | 按纸箱规格（长、宽、高）创建一条包材 Item，并可选写入供应商明细（单价、是否开票）。 |

---

## 二、调用方式

### 2.1 命令行（bench execute）

```bash
bench --site <站点名> execute 'bairun_erp.utils.api.material.item.add_packaging_material' --kwargs '<JSON 参数>'
```

示例（仅必填规格）：

```bash
bench --site site2.local execute 'bairun_erp.utils.api.material.item.add_packaging_material' --kwargs '{"br_carton_length": "0.6", "br_carton_width": "0.5", "br_carton_height": "0.4"}'
```

### 2.2 前端 / API 调用

通过 Frappe 的 `frappe.call()` 或 HTTP 请求调用白名单方法：

- **方法**：`bairun_erp.utils.api.material.item.add_packaging_material`
- **参数**：以对象形式传入，键名见下文「参数说明」。

---

## 三、参数说明

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `br_carton_length` | string | 是 | 纸箱长度。 |
| `br_carton_width`  | string | 是 | 纸箱宽度。 |
| `br_carton_height` | string | 是 | 纸箱高度。 |
| `item_code`        | string | 否 | 物料编码。不传则按规格生成，形如 `CARTON-长-宽-高`（小数点等会替换为 `_`）。 |
| `item_name`        | string | 否 | 物料名称。默认 `纸箱 长*宽*高`。 |
| `item_group`       | string | 否 | 物料组。不传默认「包材」；可传包材下的子组（如「纸箱」「其他」等）以区分包材类型，使添加更通用。 |
| `stock_uom`        | string | 否 | 库存单位。默认 `"Nos"`。 |
| `suppliers`        | string / list | 否 | 供应商列表。见下表。不传则自动取系统中**全部**供应商写入，单价、是否开票均为 0。 |
| `description`      | string | 否 | 包材 Item 的描述（Item.description）。 |

**`suppliers` 每项结构：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `supplier`         | string | 是 | 供应商名称（Link to Supplier）。 |
| `custom_price`     | number | 否 | 单价。默认 0。 |
| `custom_isinvoice` | 0 / 1  | 否 | 是否开票：1 是，0 否。默认 0。 |
| `supplier_part_no` | string | 否 | 供应商处的产品描述/料号。 |

---

## 四、返回结果

成功时返回一个对象，字段如下。

### 4.1 主表字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `item_code`        | string | 物料编码。 |
| `item_name`        | string | 物料名称。 |
| `br_carton_length` | string | 纸箱长度。 |
| `br_carton_width`  | string | 纸箱宽度。 |
| `br_carton_height` | string | 纸箱高度。 |
| `item_group`       | string | 物料组。 |
| `stock_uom`        | string | 库存单位。 |
| `description`      | string | 物料描述。 |

### 4.2 供应商明细 `supplier_items`

| 字段 | 类型 | 说明 |
|------|------|------|
| `supplier`         | string | 供应商名称。 |
| `supplier_part_no` | string | 供应商处产品描述。 |
| `custom_price`     | number | 单价。 |
| `custom_isinvoice` | 0 / 1  | 是否开票。 |

### 4.3 返回示例

**不传 `suppliers`（自动写入全部供应商，单价/开票为 0）：**

```json
{
  "item_code": "CARTON-0_5-0_4-0_3",
  "item_name": "纸箱 0.5*0.4*0.3",
  "br_carton_length": "0.5",
  "br_carton_width": "0.4",
  "br_carton_height": "0.3",
  "item_group": "包材",
  "stock_uom": "Nos",
  "description": "",
  "supplier_items": [
    {"supplier": "SUP-001", "supplier_part_no": "", "custom_price": 0.0, "custom_isinvoice": 0},
    {"supplier": "SUP-0011", "supplier_part_no": "", "custom_price": 0.0, "custom_isinvoice": 0},
    {"supplier": "SUP-001999", "supplier_part_no": "", "custom_price": 0.0, "custom_isinvoice": 0}
  ]
}
```

**传入 `suppliers`（指定单价与是否开票）：**

```json
{
  "item_code": "CARTON-0_6-0_5-0_4",
  "item_name": "纸箱 0.6*0.5*0.4",
  "br_carton_length": "0.6",
  "br_carton_width": "0.5",
  "br_carton_height": "0.4",
  "item_group": "包材",
  "stock_uom": "Nos",
  "description": "",
  "supplier_items": [
    {"supplier": "SUP-001", "supplier_part_no": "", "custom_price": 2.35, "custom_isinvoice": 1},
    {"supplier": "SUP-0011", "supplier_part_no": "", "custom_price": 2.88, "custom_isinvoice": 0},
    {"supplier": "SUP-001999", "supplier_part_no": "", "custom_price": 2.12, "custom_isinvoice": 1}
  ]
}
```

---

## 五、异常与错误

| 情况 | 错误信息 |
|------|----------|
| 未填长/宽/高或任一项为空 | `纸箱长度、纸箱宽度、纸箱高度为必填项` |
| 同一物料组下已存在相同规格（长宽高一致）的纸箱 | `该物料组下此纸箱规格已存在` |
| 传入的 `item_code` 已存在 | `物料编码 xxx 已存在，请换一个或留空由系统生成` |
| **传入的供应商在系统中不存在** | **`以下供应商不存在：xxx, yyy，不允许添加包材。`（标题：供应商无效）——此时不会创建纸箱** |
| Item 上未同步纸箱自定义字段 | `Item 上未找到自定义字段 "br_carton_length"，请先执行 migrate` |

以上均通过 `frappe.throw()` 抛出，前端/API 会得到对应错误响应。

---

## 六、调用示例汇总

### 6.1 仅必填规格（物料编码、名称、供应商均自动处理）

```bash
bench --site site2.local execute 'bairun_erp.utils.api.material.item.add_packaging_material' --kwargs '{"br_carton_length": "0.6", "br_carton_width": "0.5", "br_carton_height": "0.4"}'
```

### 6.2 指定物料名称

```bash
bench --site site2.local execute 'bairun_erp.utils.api.material.item.add_packaging_material' --kwargs '{"br_carton_length": "0.6", "br_carton_width": "0.5", "br_carton_height": "0.4", "item_name": "纸箱 0.6*0.5*0.4"}'
```

### 6.3 指定 3 家供应商及单价、是否开票

```bash
bench --site site2.local execute 'bairun_erp.utils.api.material.item.add_packaging_material' --kwargs '{
  "br_carton_length": "0.6",
  "br_carton_width": "0.5",
  "br_carton_height": "0.4",
  "item_name": "纸箱 0.6*0.5*0.4",
  "suppliers": [
    {"supplier": "SUP-001", "custom_price": 2.35, "custom_isinvoice": 1},
    {"supplier": "SUP-0011", "custom_price": 2.88, "custom_isinvoice": 0},
    {"supplier": "SUP-001999", "custom_price": 2.12, "custom_isinvoice": 1}
  ]
}'
```

---

## 七、流程简述

1. 校验必填：纸箱长、宽、高。
2. 规格唯一性：同一物料组下若已存在相同长宽高的纸箱，直接报错。
3. 物料编码：未传则生成 `CARTON-长-宽-高`；传入则校验不重复。
4. 物料组：未传则默认「包材」。
5. 创建 Item 并写入纸箱长宽高。
6. 供应商：未传 `suppliers` 则取系统全部供应商写入（单价 0、不开票）；传入则按列表写入单价与是否开票。
7. 返回新建物料的编码、名称、规格、物料组、单位及 `supplier_items` 列表。
