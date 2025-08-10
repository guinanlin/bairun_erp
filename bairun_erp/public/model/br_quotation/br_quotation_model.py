from .br_quotation_details_model import BRQuotationDetails
from datetime import date
from pydantic import BaseModel
from typing import List
from typing import Optional

class BRQuotation(BaseModel):
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
    # 客户名称 : Link - Customer
    customer_name: Optional[str] = None
    # 利润 : Currency
    profit: Optional[float] = None
    # 含税 : Check
    including_tax: Optional[bool] = None
    # 不含税价格合计 : Currency
    total_price_excluding_tax: Optional[float] = None
    # 报价的版本 : Data
    quotation_version: Optional[str] = None
    # 报价日期 : Date
    quotation_date: Optional[date] = None
    # 报价有效期 : Int - 天
    quotation_validity_period: Optional[int] = None
    # details : Table - BR Quotation Details
    details: List[BRQuotationDetails] = []
    # Amended From : Link - BR Quotation
    amended_from: Optional[str] = None
