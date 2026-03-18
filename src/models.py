from __future__ import annotations
from datetime import date
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class Treatment(str, Enum):
    EXPENSE = "expense"
    PREPAID = "prepaid"
    ACCRUAL = "accrual"
    CAPITALIZE = "capitalize"

class ApprovalLevel(str, Enum):
    AUTO = "auto_approve"
    DEPT_MANAGER = "dept_manager"
    VP_FINANCE = "vp_finance"
    DENIED = "denied"

class ServicePeriod(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def start_before_end(self) -> "ServicePeriod":
        if self.start > self.end:
            raise ValueError(f"start ({self.start}) must be <= end ({self.end})")
        return self

class LineItem(BaseModel):
    description: str
    amount: Decimal = Field(gt=0)           # Total line amount, must be positive
    unit_cost: Decimal | None = None        # For equipment: per-unit cost
    quantity: int = Field(ge=1, default=1)
    service_period: ServicePeriod | None = None  # Per-line override

class Invoice(BaseModel):
    id: str                                 # "INV-001", "UL-1", etc.
    vendor: str
    po_number: str | None                   # None = no PO
    date: date
    department: str                         # "Engineering", "Marketing", etc.
    service_period: ServicePeriod | None = None  # Invoice-level fallback
    line_items: list[LineItem]
    total: Decimal

    @model_validator(mode="after")
    def total_matches_lines(self) -> "Invoice":
        line_sum = sum(item.amount for item in self.line_items)
        if line_sum != self.total:
            raise ValueError(
                f"Invoice total ({self.total}) != sum of line items ({line_sum})"
            )
        return self

class PurchaseOrder(BaseModel):
    number: str                             # "PO-2026-044"
    vendor: str
    amount: Decimal
    department: str

class Classification(BaseModel):
    gl_code: str                            # Step 2 classifier output: "5010", "1310", etc.
    treatment: Treatment                    # EXPENSE | PREPAID | ACCRUAL | CAPITALIZE
    rule_applied: str                       # Human-readable trace: "Software/SaaS annual → 1310"
    posting_gl: str | None = None           # Set by Step 3 if different from gl_code

class JournalLine(BaseModel):
    account_code: str
    account_name: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: str

class JournalEntry(BaseModel):
    id: str                                 # Auto-generated UUID
    invoice_id: str
    date: date
    description: str
    lines: list[JournalLine]
    entry_type: str                         # "initial", "amortization", "accrual", "reversal"

    def verify(self) -> bool:
        """Debits must equal credits."""
        return sum(l.debit for l in self.lines) == sum(l.credit for l in self.lines)

class ApprovalDecision(BaseModel):
    level: ApprovalLevel                    # AUTO | DEPT_MANAGER | VP_FINANCE | DENIED
    reason: str
    approved: bool | None = None            # None = pending, True/False = decided

class POMatchResult(BaseModel):
    status: str                             # "matched", "no_po", "po_not_found", "amount_mismatch"
    matched: bool
    po: PurchaseOrder | None = None
    variance_pct: Decimal | None = None     # e.g., 0.05 for 5%
    reason: str = ""

class ClassifiedLine(BaseModel):
    line_item: LineItem
    classification: Classification | None    # None if unclassifiable

class ProcessingResult(BaseModel):
    invoice_id: str
    invoice_total: Decimal | None = None    # Stored for verification on resume
    status: str                             # "posted", "pending_approval", "flagged", "error"
    po_result: POMatchResult | None = None
    classifications: list[ClassifiedLine] = Field(default_factory=list)
    approval: ApprovalDecision | None = None
    journal_entries: list[JournalEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
