from pydantic import BaseModel

class GLAccount(BaseModel):
    code: str
    name: str
    category: str  # "Prepaid", "Asset", "Accrual", "Expense", "Liability"

GL_ACCOUNTS: dict[str, GLAccount] = {
    "1300": GLAccount(code="1300", name="Prepaid Expenses (General)", category="Prepaid"),
    "1310": GLAccount(code="1310", name="Prepaid Software", category="Prepaid"),
    "1320": GLAccount(code="1320", name="Prepaid Insurance", category="Prepaid"),
    "1500": GLAccount(code="1500", name="Fixed Assets", category="Asset"),
    "2000": GLAccount(code="2000", name="Accounts Payable", category="Liability"),
    "2100": GLAccount(code="2100", name="Accrued Expenses (General)", category="Accrual"),
    "2110": GLAccount(code="2110", name="Accrued Professional Services", category="Accrual"),
    "5000": GLAccount(code="5000", name="Office Supplies", category="Expense"),
    "5010": GLAccount(code="5010", name="Software & Subscriptions", category="Expense"),
    "5020": GLAccount(code="5020", name="Cloud Hosting & Infrastructure", category="Expense"),
    "5030": GLAccount(code="5030", name="Professional Services — Legal", category="Expense"),
    "5040": GLAccount(code="5040", name="Professional Services — Consulting", category="Expense"),
    "5050": GLAccount(code="5050", name="Marketing & Advertising", category="Expense"),
    "5060": GLAccount(code="5060", name="Travel & Entertainment", category="Expense"),
    "5070": GLAccount(code="5070", name="Facilities & Maintenance", category="Expense"),
    "5080": GLAccount(code="5080", name="Training & Development", category="Expense"),
    "5090": GLAccount(code="5090", name="Telecom & Internet", category="Expense"),
    "5100": GLAccount(code="5100", name="Insurance Expense", category="Expense"),
    "5110": GLAccount(code="5110", name="Equipment (under $5,000)", category="Expense"),
}

PREPAID_TO_EXPENSE: dict[str, str] = {
    "1310": "5010",  # Prepaid Software → Software & Subscriptions
    "1300": "5020",  # Prepaid General → Cloud Hosting
    "1320": "5100",  # Prepaid Insurance → Insurance Expense
}

def get_account(code: str) -> GLAccount:
    """Look up a GL account by code. Raises KeyError if not found."""
    if code not in GL_ACCOUNTS:
        raise KeyError(f"Unknown GL account code: {code}")
    return GL_ACCOUNTS[code]

def is_professional_service(gl_code: str) -> bool:
    """Return True if the GL code is a professional services category (5030 or 5040)."""
    return gl_code in ("5030", "5040")
