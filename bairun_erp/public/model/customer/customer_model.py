from .allowed_to_transact_with_model import AllowedToTransactWith
from .customer_credit_limit_model import CustomerCreditLimit
from .party_account_model import PartyAccount
from .portal_user_model import PortalUser
from .sales_team_model import SalesTeam
from datetime import date
from pydantic import BaseModel
from typing import List
from typing import Optional

class Customer(BaseModel):
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
    # Series : Select
    naming_series: Optional[str] = None
    # Salutation : Link - Salutation
    salutation: Optional[str] = None
    # Customer Name : Data
    customer_name: str
    # Customer Type : Select
    customer_type: str
    # Customer Group : Link - Customer Group
    customer_group: Optional[str] = None
    # Territory : Link - Territory
    territory: Optional[str] = None
    # Gender : Link - Gender
    gender: Optional[str] = None
    # From Lead : Link - Lead
    lead_name: Optional[str] = None
    # From Opportunity : Link - Opportunity
    opportunity_name: Optional[str] = None
    # From Prospect : Link - Prospect
    prospect_name: Optional[str] = None
    # Account Manager : Link - User
    account_manager: Optional[str] = None
    # Image : Attach Image
    image: Optional[str] = None
    # Billing Currency : Link - Currency
    default_currency: Optional[str] = None
    # Default Company Bank Account : Link - Bank Account
    default_bank_account: Optional[str] = None
    # Default Price List : Link - Price List
    default_price_list: Optional[str] = None
    # Is Internal Customer : Check
    is_internal_customer: Optional[bool] = None
    # Represents Company : Link - Company
    represents_company: Optional[str] = None
    # Allowed To Transact With : Table - Allowed To Transact With
    companies: List[AllowedToTransactWith] = []
    # Market Segment : Link - Market Segment
    market_segment: Optional[str] = None
    # Industry : Link - Industry Type
    industry: Optional[str] = None
    # Customer POS id : Data
    customer_pos_id: Optional[str] = None
    # Website : Data
    website: Optional[str] = None
    # Print Language : Link - Language
    language: Optional[str] = None
    # Customer Details : Text - Additional information regarding the customer.
    customer_details: Optional[str] = None
    # Customer Primary Address : Link - Address - Reselect, if the chosen address is edited after save
    customer_primary_address: Optional[str] = None
    # Primary Address : Text
    primary_address: Optional[str] = None
    # Customer Primary Contact : Link - Contact - Reselect, if the chosen contact is edited after save
    customer_primary_contact: Optional[str] = None
    # Mobile No : Read Only
    mobile_no: Optional[str] = None
    # Email Id : Read Only
    email_id: Optional[str] = None
    # Tax ID : Data
    tax_id: Optional[str] = None
    # Tax Category : Link - Tax Category
    tax_category: Optional[str] = None
    # Tax Withholding Category : Link - Tax Withholding Category
    tax_withholding_category: Optional[str] = None
    # Default Payment Terms Template : Link - Payment Terms Template
    payment_terms: Optional[str] = None
    # Credit Limit : Table - Customer Credit Limit
    credit_limits: List[CustomerCreditLimit] = []
    # Accounts : Table - Party Account - Mention if non-standard Receivable account
    accounts: List[PartyAccount] = []
    # Loyalty Program : Link - Loyalty Program
    loyalty_program: Optional[str] = None
    # Loyalty Program Tier : Data
    loyalty_program_tier: Optional[str] = None
    # Sales Team : Table - Sales Team
    sales_team: List[SalesTeam] = []
    # Sales Partner : Link - Sales Partner
    default_sales_partner: Optional[str] = None
    # Commission Rate : Float
    default_commission_rate: Optional[float] = None
    # Allow Sales Invoice Creation Without Sales Order : Check
    so_required: Optional[bool] = None
    # Allow Sales Invoice Creation Without Delivery Note : Check
    dn_required: Optional[bool] = None
    # Is Frozen : Check
    is_frozen: Optional[bool] = None
    # Disabled : Check
    disabled: Optional[bool] = None
    # Customer Portal Users : Table - Portal User
    portal_users: List[PortalUser] = []
    # 简称 : Data
    custom_short_name: Optional[str] = None
    # 统一社会信用代码 : Data
    custom_unified_credit_code: Optional[str] = None
    # 法人代表 : Data
    custom_legal_representative: Optional[str] = None
    # 成立时间 : Date
    custom_establishment_date: Optional[date] = None
    # 企业规模 : Select
    custom_enterprise_scale: Optional[str] = None
    # 员工数量 : Int
    custom_employee_count: Optional[int] = None
    # 年营业额 (万元) : Currency
    custom_annual_turnover: Optional[float] = None
    # 所属行业 : Select
    custom_industry: Optional[str] = None
    # 企业类型 : Select
    custom_enterprise_type: Optional[str] = None
    # 主营业务 : Data
    custom_main_operating_business: Optional[str] = None
    # 合作等级 : Select
    custom_cooperation_level: Optional[str] = None
    # 信用评级 : Select
    custom_credit_rating: Optional[str] = None
    # 付款方式 : Select
    custom_payment_method: Optional[str] = None
    # 信用额度 : Currency
    custom_credit_limit: Optional[float] = None
    # 累计采购金额 : Currency
    custom_accumulated_purchase_amount: Optional[float] = None
    # 合作年限 : Int
    custom_years_of_cooperation: Optional[int] = None
    # 采购偏好 : Data
    custom_purchase_prefer: Optional[str] = None
    # 开票客户名称 : Data
    custom_invoicing_customer_name: Optional[str] = None
    # 纳税人识别号 : Data
    custom_taxpayer_identification_number: Optional[str] = None
    # 开户银行 : Data
    custom_opening_bank: Optional[str] = None
    # 开户银行账号 : Data
    custom_opening_bank_account_number: Optional[str] = None
    # 联系人 : Data
    custom_contact_person: Optional[str] = None
    # 联系电话 : Data
    custom_contact_phone: Optional[str] = None
