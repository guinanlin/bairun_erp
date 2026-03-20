# BOM页面「更新产品物料清单」前端联调说明

本文给前端同学用于快速联调 `更新产品物料清单` 按钮。

---

## 1. 接口信息

- 方法路径：`bairun_erp.utils.api.material.bom_item.update_bom_from_canvas_tree`
- 调用地址：`POST /api/method/bairun_erp.utils.api.material.bom_item.update_bom_from_canvas_tree`
- 鉴权：登录态（Cookie Session）

---

## 2. 请求参数

推荐使用 `json_data` 包装参数：

```json
{
  "json_data": {
    "bom_name": "BOM-FG-001-001",
    "tree_data": "{...当前画布树JSON字符串...}",
    "update_mode": "merge"
  }
}
```

字段说明：

- `bom_name`：目标 BOM 编号（必填）
- `tree_data`：画布树数据（必填，支持 JSON 字符串或对象）
- `update_mode`：更新模式（可选）
  - `merge`（默认）：按 `item_code` 合并更新，未命中新增，不删除旧行
  - `replace`：全量替换 BOM 子项（旧行会被清空）

---

## 3. tree_data 最小可用结构

后端当前按根节点的 `children` 作为目标 BOM 子项来源。每个子节点至少包含：

- `id`（建议传，便于错误定位）
- `item_code`（必填）
- `item_name`（必填）
- `bom_qty`（必填，必须 > 0）

示例：

```json
{
  "id": "root",
  "item_name": "成品A",
  "children": [
    {
      "id": "n1",
      "item_code": "RM-001",
      "item_name": "原料1",
      "bom_qty": 2,
      "warehouse": "半成品仓库",
      "supplier": "SUP-0001"
    },
    {
      "id": "n2",
      "item_code": "RM-002",
      "item_name": "原料2",
      "bom_qty": 1.5
    }
  ]
}
```

---

## 4. 成功返回示例

```json
{
  "message": {
    "success": true,
    "message": "更新成功",
    "data": {
      "bom_name": "BOM-FG-001-001",
      "updated_items_count": 8,
      "created_items_count": 2,
      "removed_items_count": 1,
      "failed_items": []
    }
  }
}
```

---

## 5. 失败返回示例

```json
{
  "message": {
    "success": false,
    "message": "存在非法节点，请修正后重试",
    "data": {
      "bom_name": "BOM-FG-001-001",
      "updated_items_count": 0,
      "created_items_count": 0,
      "removed_items_count": 0,
      "failed_items": [
        {
          "node_id": "n9",
          "item_code": "ITEM-999",
          "error": "物料不存在"
        }
      ]
    }
  }
}
```

常见失败原因：

- `bom_name 不能为空`
- `BOM不存在或无权限更新`
- `update_mode 仅支持 merge 或 replace`
- 节点字段缺失（`item_code/item_name/bom_qty`）
- `warehouse` 无法匹配系统仓库
- `supplier` 不存在

---

## 6. curl 调试示例

> 先确保浏览器已登录同一站点，或在命令行使用可用 Cookie。

```bash
curl -X POST "http://<your-site>/api/method/bairun_erp.utils.api.material.bom_item.update_bom_from_canvas_tree" \
  -H "Content-Type: application/json" \
  -H "Cookie: sid=<your_sid>" \
  -d '{
    "json_data": {
      "bom_name": "BOM-FG-001-001",
      "tree_data": "{\"id\":\"root\",\"item_name\":\"成品A\",\"children\":[{\"id\":\"n1\",\"item_code\":\"RM-001\",\"item_name\":\"原料1\",\"bom_qty\":2}]}",
      "update_mode": "merge"
    }
  }'
```

---

## 7. 前端 fetch 示例（可直接改造）

```ts
export async function updateBomFromCanvas(params: {
  bomName: string;
  tree: Record<string, any>;
  updateMode?: "merge" | "replace";
}) {
  const resp = await fetch(
    "/api/method/bairun_erp.utils.api.material.bom_item.update_bom_from_canvas_tree",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        json_data: {
          bom_name: params.bomName,
          tree_data: JSON.stringify(params.tree),
          update_mode: params.updateMode || "merge",
        },
      }),
    }
  );

  const raw = await resp.json();
  const result = raw?.message || raw;

  if (!result?.success) {
    const firstFailed = result?.data?.failed_items?.[0];
    const detail = firstFailed
      ? `（${firstFailed.item_code || "-"}: ${firstFailed.error || "-"}）`
      : "";
    throw new Error(`${result?.message || "更新失败"}${detail}`);
  }

  return result.data;
}
```

---

## 8. 前端按钮提示文案建议

成功提示：

- 标题：`更新成功`
- 描述：`已更新 ${updated_items_count} 个物料，新增 ${created_items_count} 个，删除 ${removed_items_count} 个`

失败提示：

- 标题：`更新失败`
- 描述：`${message} + 首条 failed_items 明细`

---

## 9. 联调建议步骤

1. 先用 `merge` 模式验证“改数量 + 新增一行”。
2. 再用 `replace` 模式验证“删除节点是否生效”。
3. 故意传一个不存在的 `item_code`，验证前端错误提示是否能展示 `failed_items`。

