# 原材料物料接口 - 白名单方法与测试

## 白名单方法

- **方法**: `bairun_erp.utils.api.material.item.get_raw_material_item`
- **参数**: `item_code`（必填）, `warehouse`（可选）
- **返回**: 按 `item-field-mapping.md` 映射的主列表字段（采购相关放空/0）

## 用 bench 查看原始数据（与白名单返回对比）

**注意**：需在已安装 bairun_erp 的站点上执行。将下面命令里的 `[你的站点]` 换成实际站点名（如 `site1.local`），`test005` 换成你的物料号。

```bash
# 1. 查看 Item 主表 + 创建时间
bench --site [你的站点] execute frappe.client.get_value --kwargs '{"doctype":"Item","filters":{"name":"test005"},"fieldname":["name","item_name","stock_uom","valuation_rate","standard_rate","creation"]}'

# 2. 查看 Bin 在库数（按 item_code 汇总）
bench --site [你的站点] mariadb -e "
SELECT item_code, SUM(actual_qty) AS in_stock_qty
FROM tabBin
WHERE item_code = 'test005'
GROUP BY item_code;
"

# 3. 查看 Item Default 默认仓库
bench --site [你的站点] mariadb -e "
SELECT parent, company, default_warehouse
FROM \`tabItem Default\`
WHERE parent = 'test005';
"
```

或用一条 SQL 看 Item + Bin 汇总 + 默认仓库：

```bash
bench --site [你的站点] mariadb -e "
SELECT
  i.name AS item_code,
  i.item_name,
  i.stock_uom AS unit,
  i.valuation_rate AS inventory_cost,
  i.standard_rate AS sales_price,
  i.creation,
  (SELECT COALESCE(SUM(actual_qty), 0) FROM tabBin b WHERE b.item_code = i.name) AS in_stock_qty,
  (SELECT default_warehouse FROM \`tabItem Default\` id WHERE id.parent = i.name LIMIT 1) AS warehouse
FROM tabItem i
WHERE i.name = 'test005';
"
```

## 调用白名单方法

```bash
bench --site [你的站点] execute bairun_erp.bairun_erp.utils.api.material.item.get_raw_material_item --args '["test005"]'
```

带仓库时：

```bash
bench --site [你的站点] execute bairun_erp.bairun_erp.utils.api.material.item.get_raw_material_item --args '["test005", "Stores - _TC"]'
```

## 跑单元测试

```bash
cd /home/frappe/frappe-bench
bench --site [你的站点] run-tests --app bairun_erp --module bairun_erp.utils.api.material.test_item
```

测试会校验：不存在物料返回 error、空 item_code 返回 error、存在物料时返回结构及 Item 字段与 DB 一致（含 date、inStockQty 类型等）。
