from pydantic import BaseModel
from typing import Optional

class SalesTeam(BaseModel):
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
    # Sales Person : Link - Sales Person
    sales_person: str
    # Contact No. : Data
    contact_no: Optional[str] = None
    # Contribution (%) : Float
    allocated_percentage: Optional[float] = None
    # Contribution to Net Total : Currency
    allocated_amount: Optional[float] = None
    # Commission Rate : Data
    commission_rate: Optional[str] = None
    # Incentives : Currency
    incentives: Optional[float] = None
