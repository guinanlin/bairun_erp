# Copyright (c) 2025, Bairun and contributors
# 兼容层：将 bairun_erp.utils.api.sales_order 重定向到 sales.sales_order
# 新路径：bairun_erp.utils.api.sales.sales_order

from bairun_erp.utils.api.sales.sales_order import save_sales_order  # noqa: F401
