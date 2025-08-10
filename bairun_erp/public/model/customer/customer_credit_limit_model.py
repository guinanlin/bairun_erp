from pydantic import BaseModel
from typing import Optional

class CustomerCreditLimit(BaseModel):
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
    # Company : Link - Company
    company: Optional[str] = None
    # Credit Limit : Currency
    credit_limit: Optional[float] = None
    # Bypass Credit Limit Check at Sales Order : Check
    bypass_credit_limit_check: Optional[bool] = None
