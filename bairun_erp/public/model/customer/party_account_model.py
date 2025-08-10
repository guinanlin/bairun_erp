from pydantic import BaseModel
from typing import Optional

class PartyAccount(BaseModel):
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
    company: str
    # Default Account : Link - Account
    account: Optional[str] = None
    # Advance Account : Link - Account
    advance_account: Optional[str] = None
