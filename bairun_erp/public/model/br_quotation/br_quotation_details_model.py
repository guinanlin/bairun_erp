from pydantic import BaseModel
from typing import Optional

class BRQuotationDetails(BaseModel):
    name: str
    creation: str
    modified: str
    owner: str
    modified_by: str
    docstatus: int
    parent: Optional[str] = None
    parentfield: Optional[str] = None
    parenttype: Optional[str] = None
    idx: Optional[int] = None
    # 零件名称 : Link - Item
    part_name: Optional[str] = None
    # 模具费 : Currency
    mold_fee: Optional[float] = None
    # 材质 : Data
    material: Optional[str] = None
    # 产品单重(g) : Float
    product_unit_weight: Optional[float] = None
    # 出数 : Int
    output_number: Optional[int] = None
    # 周期 : Float
    cycle_time: Optional[float] = None
    # 产量/天 : Float
    daily_output: Optional[float] = None
    # 加工费/天 : Currency
    processing_fee_per_day: Optional[float] = None
    # 毛坯加工费 : Currency
    raw_material_processing_fee: Optional[float] = None
    # 原材料价格 : Currency
    raw_material_price: Optional[float] = None
    # 产品材料价格 : Currency
    product_material_price: Optional[float] = None
    # 产品注塑价格 : Currency
    product_injection_molding_price: Optional[float] = None
    # 工艺工位配置 : JSON
    process_station_config: Optional[str] = None
    # 合计 : Currency
    total: Optional[float] = None
