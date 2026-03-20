# BOM 删除 — 后端白名单接口说明

用于在佰润 ERP 中按 ERPNext/Frappe 标准规则删除 BOM（支持单条与批量）。

---

## 1. 接口信息

| 项目 | 说明 |
|------|------|
| 方法名 | `delete_bom` |
| 完整路径 | `bairun_erp.utils.api.material.bom_delete.delete_bom` |
| ERPNext URL | `POST /api/method/bairun_erp.utils.api.material.bom_delete.delete_bom` |
| 认证 | 需登录（`allow_guest=False`） |

---

## 2. 行为说明（与标准 Delete 一致）

该接口内部对每条 BOM 调用：

`frappe.delete_doc("BOM", name, ignore_missing=False)`

因此会自动遵循标准删除规则：

- 校验当前用户是否有 BOM 删除权限；
- 已提交单据不可删除（`docstatus=1`）；
- 被其它单据引用时会阻止删除（LinkExists）；
- 执行 `on_trash`、`after_delete` 等生命周期；
- 清理附件、标签、全局搜索、Deleted Document 记录等。

批量删除时按“逐条提交”的方式处理：单条成功即提交，单条失败则回滚该条并记录失败原因，不影响其它条继续执行。

---

## 3. 请求参数

支持两种传参方式：

1. 顶层直接传参；
2. `json_data`（推荐，支持对象或 JSON 字符串）。

### 3.1 参数列表

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` / `bom_name` / `bomName` | string | 否 | 单个 BOM 编号 |
| `names` / `bom_names` / `bomNames` / `items` | array[string] | 否 | 多个 BOM 编号 |

> 至少需要提供单条或数组中的一种；如果都传会自动合并并去重。

---

## 4. 请求示例

### 4.1 删除单个 BOM

```json
{
  "json_data": {
    "bom_name": "BOM-00001"
  }
}
```

### 4.2 批量删除 BOM

```json
{
  "json_data": {
    "items": ["BOM-00001", "BOM-00002", "BOM-00003"]
  }
}
```

---

## 5. 返回示例

### 5.1 全部成功

```json
{
  "success": true,
  "message": "已删除 3 张 BOM",
  "data": {
    "deleted": ["BOM-00001", "BOM-00002", "BOM-00003"],
    "failed": []
  }
}
```

### 5.2 部分成功

```json
{
  "success": false,
  "message": "部分成功：已删除 1 张，失败 2 张",
  "data": {
    "deleted": ["BOM-00001"],
    "failed": [
      {
        "name": "BOM-00002",
        "message": "Not permitted"
      },
      {
        "name": "BOM-00003",
        "message": "Cannot delete or cancel because BOM-00003 is linked with ..."
      }
    ]
  }
}
```

---

## 6. 常见失败原因

- 当前用户缺少 BOM 的删除权限；
- BOM 已提交，不允许删除；
- BOM 被生产单、物料请求、其他业务单据引用；
- BOM 编号不存在（`ignore_missing=False`）。

---

## 7. 联调建议

- 前端可直接透传勾选列表到 `items`；
- 对返回里的 `data.failed[]` 做逐条展示，方便用户定位失败单据；
- 若只想“全成功才提示成功”，可依据 `success` 字段判断；若需部分成功提示，优先使用 `message + failed` 明细。
