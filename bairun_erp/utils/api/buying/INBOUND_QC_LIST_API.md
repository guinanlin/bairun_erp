# 入库质检列表 / 详情 — 调用说明

面向前端（或网关）：与 `get_purchase_order_unfulfilled_list`、`submit_quality_inspection_and_stock_entry` 相同方式调用 Frappe 白名单方法（需登录 Cookie 或 `Authorization: Bearer <token>`）。

## 1. 入库质检列表

- **方法路径**: `bairun_erp.utils.api.buying.quality_inspection_inbound_list.get_inbound_qc_list`
- **HTTP**: `POST /api/method/bairun_erp.utils.api.buying.quality_inspection_inbound_list.get_inbound_qc_list`
- **Content-Type**: `application/json`

### 请求体（`json_data`）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `limit_start` | int | 0 | 分页起始 |
| `limit_page_length` | int | 20 | 每页条数，**最大 100**（超出按 100 截断） |
| `order_by` | string | 见下 | 仅允许：`posting_date`、`purchase_receipt`、`idx`、`creation` + `asc`/`desc`，逗号分隔 |
| `qc_line_status` | string | `all` | `all` / `pending` / `done` |
| `search_purchase_receipt` | string | — | PR 单号模糊 |
| `search_supplier` | string | — | 供应商编号或名称模糊 |
| `search_item` | string | — | 物料编码或名称模糊 |
| `search_purchase_order` | string | — | 采购订单号模糊 |
| `search_sales_order` | string | — | 销售订单 / `customer_order`（若 PO 有该字段）模糊 |
| `from_posting_date` | string | — | PR 过账日起 `YYYY-MM-DD` |
| `to_posting_date` | string | — | PR 过账日止 `YYYY-MM-DD` |
| `qi_status` | string | — | `Accepted` / `Rejected`；仅在 `qc_line_status` 为 `all` 或 `done` 时生效，按**最新一笔**已提交 QI 的结论筛选 |

默认排序：`posting_date desc, purchase_receipt desc, idx asc`（SQL 层为 `pr.posting_date`、`pr.name`、`pri.idx`）。

### 响应

顶层与其它接口一致，由 Frappe 包装；业务数据在 **`message`** 内：

```json
{
  "message": {
    "items": [ /* 行对象 */ ],
    "total_count": 123
  }
}
```

单行重要字段：`purchase_receipt`、`pr_item_name`（与提交质检入参一致）、`qc_line_status`（`pending`/`done`）、`qty`（PR 行数量）、`quality_inspection_count`；已检时含 `quality_inspection`、`qi_status`、`qi_inspected_qty`（优先 良品+次品，否则 `sample_size`）、`stock_entry` 等。

### curl 示例

```bash
curl -s -X POST 'https://<host>/api/method/bairun_erp.utils.api.buying.quality_inspection_inbound_list.get_inbound_qc_list' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: token <api_key>:<api_secret>' \
  --data-binary '{"json_data":{"limit_start":0,"limit_page_length":20,"qc_line_status":"pending"}}'
```

## 2. 入库质检单行详情（可选）

- **方法路径**: `bairun_erp.utils.api.buying.quality_inspection_inbound_list.get_inbound_qc_line_detail`
- **HTTP**: `POST ...get_inbound_qc_line_detail`

### 请求体

| 字段 | 必填 |
|------|------|
| `purchase_receipt` | 是 |
| `pr_item_name` | 是 |

### 响应

`message` 内含 PR 行基础字段、`quality_inspection_list`（全部已提交 QI，新在前）、`stock_entries`（关联 Material Receipt 入库单号列表）、`quality_inspection_count` 等。

## 3. 网关白名单

若前端经网关 `getErpnextResource` 等方法访问，请将上述 **完整方法路径** 与 `get_purchase_order_unfulfilled_list`、`submit_quality_inspection_and_stock_entry` 一并加入同一白名单，否则可能返回 403。

## 4. 提交质检

列表仅查询；提交仍调用 **`submit_quality_inspection_and_stock_entry`**，传入本行的 `purchase_receipt`、`pr_item_name`。

## 5. 联调报错：HTTP 417 / `No module named '...quality_inspection_inbound_list'`

### 含义

ERPNext 在解析 `cmd` 时需要 `import bairun_erp.utils.api.buying.quality_inspection_inbound_list`。若当前 **bench 目录下没有对应 `.py` 文件**（未合并代码、未发布到该主机、或 FastAPI 的 `ERPNEXT_BASE_URL` 指向了旧环境），会抛出 **`frappe.exceptions.ValidationError`**，HTTP 常为 **417**，正文含 **Failed to get method for command** 与 **No module named ...**。

前端路径与参数一般无需修改；需在 **与网关一致的 ERPNext 站点** 上补齐代码并重启进程。

### 实施检查清单

1. **确认文件存在**（在运行 ERPNext 的 bench 上执行）：
   ```bash
   ls -la apps/bairun_erp/bairun_erp/utils/api/buying/quality_inspection_inbound_list.py
   ```
   若 `No such file`，说明该环境未拉到包含本接口的 `bairun_erp` 版本（需 `git pull` / 发版 / 同步 apps）。

2. **确认包路径**：同目录下应有 `bairun_erp/utils/api/buying/__init__.py`（与其它子包一致），避免个别打包/同步工具忽略「非包目录」。

3. **重载 Python 进程**（按贵司规范任选）：
   ```bash
   bench restart
   ```
   若使用 gunicorn/supervisor 多进程，需确保 **worker 已全部重启**，否则仍可能短暂命中旧代码。

4. **白名单**：FastAPI / 网关需放行完整方法名（见上文 §3）；仅 ERPNext 侧有文件仍可能被网关 403，但不会出现 `No module named`。

5. **冒烟**（在 **已安装 bairun_erp 的站点** 上，将 `site2.local` 换成实际站点名）：
   ```bash
   bench --site site2.local execute \
     bairun_erp.utils.api.buying.quality_inspection_inbound_list.get_inbound_qc_list \
     --kwargs '{"json_data":{"limit_page_length":1}}'
   ```
   无 Traceback、且业务上可在 `frappe.response` 中看到 `message` 即表示模块已加载成功。

### 联调通过标准（与前端一致）

- `/scm/qc/inbound` 不再出现「加载失败」类 Toast。
- 接口成功时 `message` 为 `{ "items": [], "total_count": n }` 结构（允许真实空列表），且 **`total_count` 与分页逻辑一致**。
