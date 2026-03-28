# 按物料编码更新 Item 属性（白名单 API）

## 方法名

`bairun_erp.utils.api.material.item_properties_update.update_item_properties_by_item_code`

## HTTP 调用

- **URL**：`POST /api/method/bairun_erp.utils.api.material.item_properties_update.update_item_properties_by_item_code`
- **认证**：需登录（与站点 Session / API Key 一致）。
- **Content-Type**：`application/x-www-form-urlencoded` 或 `application/json`（Frappe 常用 form：`cmd` + 各参数）。

### 推荐传参方式

二选一即可：

1. **顶层参数**：`item_code` + 其余白名单字段平铺（或再配合 `json_data` 对象）。
2. **json_data**：JSON 字符串或对象，内含 `item_code` 及要更新的字段。

未出现在请求里的字段 **不会** 被修改；子表 **`br_process_suppliers` / `br_packaging_details` / `br_pallet_selections` 若传入则整表替换**（先清空再按数组写入）。

### 请求体示例（JSON 思路）

```json
{
  "item_code": "YOUR-ITEM-CODE",
  "item_name": "可选，改显示名",
  "warehouse": "可选，默认仓库（会解析到当前用户默认公司下的 Warehouse）",
  "br_packing_qty": 24,
  "br_turnover": 1,
  "br_carton_spec": "纸箱物料编码或箱规 Item 的 name",
  "br_volume": "0.05",
  "br_carton_length": "40",
  "br_carton_width": "30",
  "br_carton_height": "25",
  "br_supplier": "供应商名或编码（与 Item 字段类型一致）",
  "br_price": 12.5,
  "br_quality_inspection": 1,
  "br_has_mark": 0,
  "br_mark_document": "唛头文档 URL 或标识",
  "br_mark_document_name": "文件名",
  "custom_work_instruction_url": "作业指导书图片/文件 URL（建议先上传再传链接）",
  "br_packaging_details": [
    {
      "br_packaging_item": "吸塑",
      "br_packaging_model": "型号",
      "br_packaging_ratio": "1:1",
      "br_reusable": 1,
      "br_supplier_one": "SUPPLIER-NAME",
      "br_price_one": 1.0
    }
  ],
  "br_process_suppliers": [
    {
      "br_process": "组装",
      "br_workstation": "一工位",
      "br_supplier_one": "SUPPLIER-NAME",
      "br_price_one": 2.0
    }
  ],
  "br_pallet_selections": [
    {
      "br_pallet_model": "木",
      "br_pallet_size": "1200x1000",
      "br_palletizing_height": "1.8m",
      "br_qty_per_pallet": 40,
      "br_pallet_unit_price": 50,
      "br_pallet_per_layer": 8,
      "br_pallet_item_code": "PALLET-SKU",
      "br_pallet_spec": "展示用说明"
    }
  ]
}
```

### 成功 / 失败响应（示意）

```json
{ "success": true, "message": "updated", "data": { "item_code": "...", "modified": "..." } }
```

```json
{ "success": false, "message": "错误说明" }
```

## 与 BOM 画布接口的关系

- **`create_bom_from_canvas_tree`**：创建/校验节点 Item 时，若节点带 `item_attrs`（及顶层 `warehouse`），会通过同一套 `apply_item_attrs` 写入；与上述白名单字段一致（子表同样 **整表覆盖**）。
- **`update_bom_from_canvas_tree`**：**只更新 BOM 子件行**（merge/replace），**不会**根据树节点回写 Item 主档/子表。若仅改 BOM 结构而不跑创建流程，需由前端另调本接口或再走创建流程以同步物料属性。

## 部署说明

若使用本次新增的子表列（包材 `br_reusable`、托盘 `br_pallet_unit_price` 等），部署后需对站点执行：`bench --site <站点名> migrate`。
