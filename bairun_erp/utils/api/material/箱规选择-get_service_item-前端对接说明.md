# 箱规选择 - Item 供应商列表 - 后端接口回复说明

本文档为后端对《箱规选择 - Item 供应商列表 - 后端接口需求说明》的正式回复，供前端工程师对接使用。

---

## 请前端工程师进行调试与确认

**接口已按需求完成扩展并部署**，请前端按本文档进行联调与确认：

1. **调用接口**：`get_service_item`（见下文「二、应调用的接口」）。
2. **场景**：用户在 BOM 画布/物料包装的箱规选择（BrCartonSelect）里选中某个纸箱后，用该纸箱的 `item_code` 调本接口，应能拿到：Item 基本信息、**长宽高（厘米）**、**体积（立方米）**、**完整供应商列表**。
3. **请确认**：返回字段名与类型是否符合预期、供应商列表能否正确展示、长宽高与体积数值是否正确（体积 = 长×宽×高÷1000000）。
4. **问题反馈**：联调中若发现返回结构不一致、缺字段、或需要补充字段，请反馈给后端，便于及时调整。

下文为完整接口说明与调用方式，可直接用于实现与调试。

---

## 一、结论摘要

| 问题 | 回复 |
|------|------|
| **是否有现成接口可返回「某 Item 的供应商列表」？** | **有**。使用白名单方法 `get_service_item`，按 `item_code`（或 `item_name`）查询即可返回该 Item 的基本信息 + **完整供应商列表**（Item Supplier 子表）。 |
| **是否支持 Item Group = 纸箱？** | **支持**。接口未限制物料组，传纸箱的 `item_code` 即可查到该纸箱的供应商列表；也可传 `item_group="纸箱"` 做校验或按名称解析时缩小范围。 |
| **长宽高与体积是否返回？** | **已扩展**。同一接口现已返回纸箱长宽高（`br_carton_length/width/height`，单位**厘米**）及体积 **`br_volume_m3`**（单位**立方米**，= 长×宽×高÷1000000）。非纸箱或未维护长宽高时，上述字段为 `null`。 |
| **是否需要前端单独再写/再调其他接口？** | **不需要**。箱规选择场景下：用户选择纸箱后，用该纸箱的 `item_code` 调一次 `get_service_item` 即可拿到：Item 基本信息、长宽高、体积、供应商列表，无需再调列表或详情接口拼数据。 |

---

## 二、应调用的接口

### 2.1 方法信息

| 项目 | 说明 |
|------|------|
| **方法名** | `get_service_item` |
| **模块路径** | `bairun_erp.utils.api.material.item_services` |
| **白名单** | 是（`@frappe.whitelist()`） |
| **用途** | 按物料编码/名称查询某 Item 的详情及**完整供应商列表**（Item Supplier 子表）；若为纸箱等带长宽高的物料，一并返回长宽高（厘米）与体积（立方米）。 |

### 2.2 调用方式

**前端推荐**：使用 Frappe 的 `frappe.call()`：

```javascript
frappe.call({
  method: 'bairun_erp.utils.api.material.item_services.get_service_item',
  args: {
    item_code: '选中的纸箱 item_code（即 Item.name）'
    // 可选：item_group: '纸箱'  —— 按名称解析时限定在纸箱组
  }
}).then((r) => {
  if (r.message) {
    const { item_code, item_name, supplier_items, br_carton_length, br_carton_width, br_carton_height, br_volume_m3 } = r.message;
    // 使用供应商列表、长宽高、体积进行展示/回填
  }
});
```

**HTTP 方式**（若不用 frappe.call）：

- **URL**：`POST /api/method/bairun_erp.utils.api.material.item_services.get_service_item`
- **Content-Type**：`application/x-www-form-urlencoded` 或 `application/json`（按站点配置）
- **参数**：`item_code`（或 `item_name`），可选 `item_group`

---

## 三、入参与返回结构

### 3.1 入参

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `item_code` | string | 与 item_name 二选一 | 物料编码（Item.name）。箱规选择时即用户选中的纸箱编码。 |
| `item_name` | string | 与 item_code 二选一 | 物料名称。按名称查找时使用。 |
| `item_group` | string | 否 | 物料组。用于按 item_name 解析时缩小范围或校验；传 `"纸箱"` 表示只在该组下查。 |

### 3.2 返回结构（JSON）

| 字段 | 类型 | 说明 |
|------|------|------|
| `item_code` | string \| null | 物料编码。未查到 Item 时为 null。 |
| `item_name` | string \| null | 物料名称。 |
| `item_group` | string \| null | 物料组。 |
| `stock_uom` | string \| null | 库存单位。 |
| `description` | string \| null | 描述。 |
| **`supplier_items`** | array | **该 Item 的完整供应商列表**（Item Supplier 子表）。见下表。 |
| **`br_carton_length`** | number \| null | 纸箱长度，**单位：厘米**。非纸箱或未维护时为 null。 |
| **`br_carton_width`** | number \| null | 纸箱宽度，**单位：厘米**。 |
| **`br_carton_height`** | number \| null | 纸箱高度，**单位：厘米**。 |
| **`br_volume_m3`** | number \| null | 纸箱体积，**单位：立方米**。计算公式：`(长/100) × (宽/100) × (高/100)`。三者任一无则 null。 |

**`supplier_items[]` 元素结构**（与需求文档期望一致）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `supplier` | string | 供应商编码（Link to Supplier）。 |
| `supplier_name` | string | 供应商名称（接口侧查 Supplier 表带出）。 |
| `supplier_part_no` | string | 供应商料号。 |
| `custom_price` | number | 单价（自定义字段）。 |
| `custom_isinvoice` | number | 是否开票（自定义字段）。 |

### 3.3 返回示例

**查到纸箱且带长宽高时**（例如长 50cm、宽 40cm、高 30cm）：

```json
{
  "item_code": "CARTON-50-40-30",
  "item_name": "纸箱 50*40*30",
  "item_group": "纸箱",
  "stock_uom": "Nos",
  "description": "",
  "supplier_items": [
    { "supplier": "供应商A", "supplier_name": "xxx", "supplier_part_no": "", "custom_price": 1.23, "custom_isinvoice": 1 },
    { "supplier": "供应商B", "supplier_name": "yyy", "supplier_part_no": "", "custom_price": 1.45, "custom_isinvoice": 0 }
  ],
  "br_carton_length": 50,
  "br_carton_width": 40,
  "br_carton_height": 30,
  "br_volume_m3": 0.06
}
```

**未查到 Item 时**（如该类目尚未配置）：

```json
{
  "item_code": null,
  "item_name": "传入的 item_name 或 null",
  "item_group": null,
  "stock_uom": null,
  "description": null,
  "supplier_items": [],
  "br_carton_length": null,
  "br_carton_width": null,
  "br_carton_height": null,
  "br_volume_m3": null
}
```

---

## 四、前端使用方式建议

1. **箱规选择（BrCartonSelect）**：用户选择纸箱后，前端已拿到该纸箱的 `item_code`（即 `name`）。
2. **拉取详情与供应商列表**：使用该 `item_code` 调用一次 `get_service_item`，即可得到：
   - Item 基本信息（item_code、item_name、item_group、stock_uom、description）；
   - 长宽高（br_carton_length/width/height，厘米）与体积（br_volume_m3，立方米）；
   - 供应商列表（supplier_items），用于展示、回填或后续业务。
3. **无需再调** `GET /api/resource/Item` 列表或详情来拼长宽高或供应商，避免多次请求与字段不一致。

---

## 五、Item Supplier 子表说明（供参考）

- **存储**：标准子表 `tabItem Supplier`，`parent` 为 Item 的 name，`parenttype = "Item"`，`parentfield = "supplier_items"`。
- **自定义字段**：`custom_price`、`custom_isinvoice`、`custom_pricing_factor` 等；当前接口返回中已包含 `custom_price`、`custom_isinvoice`，若需 `custom_pricing_factor` 可向后端提出补充。

---

## 六、联调检查清单（请前端确认）

联调时建议逐项确认：

| 序号 | 检查项 | 说明 |
|------|--------|------|
| 1 | 接口可调通 | 使用 `item_code`（纸箱编码）调用 `get_service_item`，返回 200，`r.message` 为对象。 |
| 2 | 基本信息完整 | `item_code`、`item_name`、`item_group`、`stock_uom`、`description` 与预期一致。 |
| 3 | 供应商列表 | `supplier_items` 为数组，元素含 `supplier`、`supplier_name`、`supplier_part_no`、`custom_price`、`custom_isinvoice`；纸箱在 Item 主档已维护的供应商应全部出现。 |
| 4 | 长宽高（厘米） | 纸箱 Item 有维护长宽高时，`br_carton_length`、`br_carton_width`、`br_carton_height` 为数字（单位厘米）；未维护或非纸箱时为 `null`。 |
| 5 | 体积（立方米） | 有长宽高时 `br_volume_m3` 为数字，且等于 `(长/100)*(宽/100)*(高/100)`；否则为 `null`。 |
| 6 | 未查到 Item | 传入不存在的 `item_code` 时，返回中 `item_code` 为 `null`，`supplier_items` 为空数组，长宽高与体积为 `null`，不报错。 |

确认无误或有问题请反馈后端，以便关闭需求或做补充修改。

---

*文档版本：供前端调试与确认 | 后端已实现 get_service_item 扩展（长宽高 + br_volume_m3）*
