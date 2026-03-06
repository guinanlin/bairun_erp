# 包材价格页接口调用文档

本文档说明包材价格页两个白名单方法的调用方式、参数、返回结构及 bench execute 示例。

---

## 一、接口概览

| 接口 | 方法名 | 模块路径 | 用途 |
|------|--------|----------|------|
| 按分类获取包材列表（分页） | `get_packaging_material_page` | `bairun_erp.utils.api.material.item_packaging` | 进入/切换包材分类时拉取规格列表、供应商配置、各规格下各供应商单价 |
| 供应商历史单价 | `get_supplier_price_history` | `bairun_erp.utils.api.material.item_packaging` | 点击「查看历史单价」时拉取该供应商的历史单价记录 |
| 按物料组批量应用供应商价格 | `apply_supplier_prices_by_item_group` | `bairun_erp.utils.api.material.item_packaging` | 「供应商价格审核」：将该物料组下全部规格的供应商价格统一为页顶配置 |

---

## 二、按分类获取包材列表（分页）

### 2.1 方法信息

- **方法名**：`get_packaging_material_page`
- **白名单**：是（`@frappe.whitelist()`）
- **用途**：按包材分类（物料组）分页返回该分类下的供应商列表 + 规格列表（长宽高、描述、各供应商单价与是否开票）。

### 2.2 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `item_group` | string | 二选一 | 物料组名称，如「纸箱」「包材」「泡沫垫板」。与 `category` 二选一。 |
| `category` | string | 二选一 | 分类 key，如 `box`、`foam-pad`。与 `item_group` 二选一，会映射为物料组。 |
| `page` | int | 否 | 页码，从 1 开始，默认 1。 |
| `page_size` | int | 否 | 每页条数，默认 20，最大 500。 |

**分类 key 与物料组对应关系**（`category` → `item_group`）：

| category | item_group |
|----------|------------|
| box | 纸箱 |
| foam-pad | 泡沫垫板 |
| laminated-foam-pad | 覆膜泡沫垫板 |
| foam-tray | 泡沫坑盘 |
| laminated-foam-tray | 覆膜泡沫坑盘 |
| foam-edge-pad | 泡沫护边垫板 |
| laminated-foam-edge-pad | 覆膜泡沫护边垫板 |
| pe-film | PE膜 |
| dust-free-paper | 无尘纸 |
| dust-bag | 防尘袋 |
| blister | 吸塑 |
| transparent-tape | 透明胶带 |
| red-tape | 红色胶带 |
| stretch-film | 缠绕膜 |

### 2.3 响应结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `item_group` | string | 分类显示名（物料组），如「纸箱」。 |
| `category` | string | 分类 key，如 `box`；若传入的物料组不在映射表则为空字符串。 |
| `suppliers` | array | 该分类下出现过的供应商列表（表头/页顶配置用）。 |
| `suppliers[].id` | string | 供应商主键（Supplier.name）。 |
| `suppliers[].name` | string | 供应商名称（展示用）。 |
| `suppliers[].unit_price` | number \| null | 当前/默认单价。 |
| `suppliers[].invoice_enabled` | boolean | 是否开票。 |
| `specs` | array | 规格行列表（当前页）。 |
| `specs[].id` | string | 规格对应物料编码（item_code）。 |
| `specs[].length` | number \| null | 长。 |
| `specs[].width` | number \| null | 宽。 |
| `specs[].height` | number \| null | 高。 |
| `specs[].product_requirements` | string | 产品要求（Item.description）。 |
| `specs[].amounts` | (number \| null)[] | 与 `suppliers` 顺序一致，每个供应商在该规格下的单价；无则 null。 |
| `total` | int | 该分类下规格总数（用于分页）。 |
| `page` | int | 当前页码。 |
| `page_size` | int | 当前每页条数。 |

### 2.4 调用示例

**bench execute（物料组=纸箱）**：

```bash
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"item_group": "纸箱", "page": 1, "page_size": 20}'
```

**bench execute（按分类 key）**：

```bash
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"category": "box", "page": 1, "page_size": 20}'
```

**前端 / API**：调用白名单方法 `bairun_erp.utils.api.material.item_packaging.get_packaging_material_page`，传入上述参数对象即可。

### 2.5 异常

| 情况 | 错误信息 |
|------|----------|
| 未传 `item_group` 且未传 `category`，或 `category` 不在映射表 | `请传入 item_group 或 category（如 纸箱 / box）` |

---

## 三、供应商历史单价

### 3.1 方法信息

- **方法名**：`get_supplier_price_history`
- **白名单**：是（`@frappe.whitelist()`）
- **用途**：获取某供应商的历史单价记录，用于「查看历史单价」弹窗。当前以 Item Supplier 行作为记录，`effective_date` 取该行 creation。

### 3.2 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `supplier_id` | string | 是 | 供应商主键（Supplier.name）。 |
| `item_group` | string | 否 | 只返回该物料组下的规格记录。 |
| `item_code` | string | 否 | 只返回该物料的记录。 |

### 3.3 响应结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `supplier_id` | string | 供应商主键。 |
| `supplier_name` | string | 供应商名称（展示用）。 |
| `history` | array | 按时间倒序的历史记录。 |
| `history[].unit_price` | number \| null | 单价。 |
| `history[].effective_date` | string | 生效/合作日期，格式 `YYYY-MM-DD`（当前为 Item Supplier 行的 creation）。 |
| `history[].item_code` | string | 关联规格的 item_code。 |

### 3.4 调用示例

**bench execute**：

```bash
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_supplier_price_history --kwargs '{"supplier_id": "SUP-001", "item_group": "纸箱"}'
```

仅按供应商查询（不按物料组/物料过滤）：

```bash
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_supplier_price_history --kwargs '{"supplier_id": "SUP-001"}'
```

### 3.5 异常

| 情况 | 错误信息 |
|------|----------|
| 未传 `supplier_id` | 需传入 supplier_id（逻辑校验） |
| 供应商不存在 | `供应商不存在`（标题：供应商无效） |

---

## 四、bench execute 入口汇总

站点 `site2.local` 请按实际替换。

```bash
# 1. 按物料组「纸箱」查询包材列表
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"item_group": "纸箱", "page": 1, "page_size": 20}'

# 2. 按分类 key「box」查询（等价纸箱）
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"category": "box", "page": 1, "page_size": 20}'

# 3. 按物料组「包材」查询
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_packaging_material_page --kwargs '{"item_group": "包材", "page": 1, "page_size": 20}'

# 4. 供应商历史单价（可选 item_group / item_code）
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.get_supplier_price_history --kwargs '{"supplier_id": "SUP-001", "item_group": "纸箱"}'

# 5. 按物料组批量应用供应商价格（供应商价格审核）
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.apply_supplier_prices_by_item_group --kwargs '{"item_group": "纸箱", "suppliers": [{"supplier": "SUP-001", "custom_price": 2.5, "custom_isinvoice": 1}, {"supplier": "SUP-0011", "custom_price": 1.9, "custom_isinvoice": 0}]}'
```

---

## 五、按物料组批量应用供应商价格

### 5.1 方法信息

- **方法名**：`apply_supplier_prices_by_item_group`
- **白名单**：是（`@frappe.whitelist()`）
- **用途**：将该物料组下**所有**包材 Item 的供应商明细统一为入参 `suppliers` 配置；用于包材价格页「供应商价格审核」按钮。

### 5.2 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `item_group` | string | 二选一 | 物料组名称，如「纸箱」「泡沫垫板」。与 `category` 二选一。 |
| `category` | string | 二选一 | 分类 key，如 `box`、`foam-pad`。与 `item_group` 二选一，会映射为物料组。 |
| `suppliers` | array | 是 | 供应商配置列表（建议最多 3 条，与页顶槽位一致）。 |
| `suppliers[].supplier` | string | 是 | 供应商主键（Supplier.name）。 |
| `suppliers[].custom_price` | number | 否 | 单价，缺省为 0。 |
| `suppliers[].custom_isinvoice` | 0 \| 1 | 否 | 是否开票，0/1，缺省 0。 |
| `suppliers[].supplier_part_no` | string | 否 | 供应商料号，可选。 |

### 5.3 响应结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 是否全部成功。 |
| `message` | string | 成功提示或错误信息。 |
| `updated_count` | int | 实际更新的 Item 数量。 |
| `item_group` | string | 当前物料组（回显）。 |

### 5.4 调用示例

**bench execute**：

```bash
bench --site site2.local execute bairun_erp.utils.api.material.item_packaging.apply_supplier_prices_by_item_group --kwargs '{
  "item_group": "纸箱",
  "suppliers": [
    {"supplier": "SUP-001", "custom_price": 2.23, "custom_isinvoice": 1},
    {"supplier": "SUP-0011", "custom_price": 1.88, "custom_isinvoice": 0},
    {"supplier": "SUP-001999", "custom_price": 2.12, "custom_isinvoice": 1}
  ]
}'
```

### 5.5 异常与错误

- 未传 `item_group` 且未传 `category`：返回 `success: false`，`message`: 请传入 item_group 或 category。
- 未传 `suppliers` 或空：返回 `success: false`，`message`: 请传入 suppliers 供应商配置列表。
- 传入的供应商不存在：返回 `success: false`，`message`: 以下供应商不存在：xxx，不允许添加包材。（与 add_packaging_material 一致）
- 更新某条 Item 失败：返回 `success: false`，`message`: 更新物料 xxx 时失败：xxx，`updated_count` 为已成功数量。

---

## 六、与前端约定

- **规格列表、供应商配置**：由 `get_packaging_material_page` 返回的 `suppliers`、`specs` 替换前端 mock；`specs[].amounts` 与 `suppliers` 顺序一致。
- **历史单价弹窗**：由 `get_supplier_price_history` 返回的 `history` 展示；前端「合作时间」对应 `effective_date`。
- **新增规格**：继续使用 `add_packaging_material`（见 `add_packaging_material.md`）。
- **供应商价格审核**：点击「供应商价格审核」时，将当前页顶的 `suppliers`（id、单价、开票）与当前物料组组装为请求参数，调用 `apply_supplier_prices_by_item_group`；成功则展示「已更新 N 个规格」或后端返回的 `message`，失败则展示 `message`。
