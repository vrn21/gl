# Phase 1 — Data Foundation

> **Depends on:** Nothing (first phase)
> **Produces:** All data models, fixtures, chart of accounts, SQLite storage — the foundation all other phases build on.
> **Estimated effort:** ~4 hours

---

## Goal

Build the data layer. After this phase, every model can be instantiated, serialized to JSON, deserialized back, and persisted to SQLite. The fixtures for all 16 invoices and their purchase orders are ready. No pipeline logic yet.

---

## 1. Project Setup

### 1.1 Dependencies

Add to `pyproject.toml`:

```toml
[project]
name = "varick"
version = "0.1.0"
description = "AI agent for Accounts Payable automation"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.0",
    "click>=8.0",
]

[project.scripts]
gl = "main:cli"
```

### 1.2 Directory Structure

Create the following directory structure:

```
varick/
├── main.py                          # CLI entry point (empty stub for now)
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── models.py                    # ALL data models
│   ├── chart_of_accounts.py         # GL account registry
│   ├── store.py                     # SQLite persistence
│   └── fixtures.py                  # Load invoices + POs from JSON
├── data/
│   ├── invoices.json                # All 16 invoices
│   └── purchase_orders.json         # All PO fixtures
└── tests/
    └── test_models.py               # Model validation tests
```

Create empty `__init__.py` files as needed.

---

## 2. Data Models (`src/models.py`)

> **Single file, ~200 lines. Do not split into multiple files.**

All models are **Pydantic v2 `BaseModel`** with strict validation. Use `Decimal` for all monetary values, never `float`.

### Required Imports

```python
from __future__ import annotations
from datetime import date
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field, model_validator
```

### 2.1 Enums

```python
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
```

### 2.2 Invoice Domain Models

```python
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
```

### 2.3 Classification Output Models

```python
class Classification(BaseModel):
    gl_code: str                            # Step 2 classifier output: "5010", "1310", etc.
    treatment: Treatment                    # EXPENSE | PREPAID | ACCRUAL | CAPITALIZE
    rule_applied: str                       # Human-readable trace: "Software/SaaS annual → 1310"
    posting_gl: str | None = None           # Set by Step 3 if different from gl_code
```

> **Important:** `Classification` is intentionally mutable. The recognition engine (Phase 2) will override `treatment` and set `posting_gl` in-place for accrual entries. This is a conscious design choice documented in the master DESIGN.md §3.

### 2.4 Journal Entry Models

```python
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
```

### 2.5 Approval Model

```python
class ApprovalDecision(BaseModel):
    level: ApprovalLevel                    # AUTO | DEPT_MANAGER | VP_FINANCE | DENIED
    reason: str
    approved: bool | None = None            # None = pending, True/False = decided
```

### 2.6 PO Match Result

```python
class POMatchResult(BaseModel):
    status: str                             # "matched", "no_po", "po_not_found", "amount_mismatch"
    matched: bool
    po: PurchaseOrder | None = None
    variance_pct: Decimal | None = None     # e.g., 0.05 for 5%
    reason: str = ""
```

### 2.7 Processing Result (Pipeline Output)

```python
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
```

---

## 3. Chart of Accounts (`src/chart_of_accounts.py`)

### 3.1 GLAccount Model

```python
from pydantic import BaseModel

class GLAccount(BaseModel):
    code: str
    name: str
    category: str  # "Prepaid", "Asset", "Accrual", "Expense", "Liability"
```

### 3.2 Account Registry

```python
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
```

### 3.3 Helper Functions and Mappings

```python
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
```

---

## 4. JSON Fixtures

### 4.1 Format Specification

All invoices go in `data/invoices.json` as a JSON array. All POs go in `data/purchase_orders.json` as a JSON array. Use string representation for Decimal values (e.g., `"24000.00"` not `24000`).

### 4.2 Labeled Invoices (6 total)

Construct these from REQS.md §Sample Invoices. Key details that MUST be correct:

| Invoice | Vendor | PO | Date | Dept | Total | Key fixture details |
|---------|--------|----|------|------|-------|-------------------|
| INV-001 | Cloudware Solutions | PO-2026-044 | 2026-01-05 | Engineering | $24,000 | 1 line item. `service_period: {start: "2026-01-01", end: "2026-12-31"}` on the line item. |
| INV-002 | Morrison & Burke LLP | PO-2026-051 | 2026-01-20 | Legal | $9,500 | 3 line items: $4,500 + $3,200 + $1,800. No service periods needed. |
| INV-003 | TechDirect Inc. | PO-2026-038 | 2026-02-01 | Engineering | $49,900 | 3 line items. MacBook: `unit_cost: "1800.00", quantity: 3, amount: "5400.00"`. Server: `amount: "8500.00"`. AWS: `amount: "36000.00"`, `service_period: {start: "2026-02-01", end: "2027-01-31"}`. |
| INV-004 | Apex Strategy Group | PO-2025-189 | 2026-01-15 | Operations | $8,700 | 2 line items: $7,500 + $1,200. **Invoice-level** `service_period: {start: "2025-12-01", end: "2025-12-31"}` (Dec 2025). |
| INV-005 | BrightSpark Agency | PO-2026-062 | 2026-02-10 | Marketing | $23,500 | 4 line items: $15,000 + $2,000 + $5,000 + $1,500. No service periods. |
| INV-006 | QuickPrint Co. | `null` (no PO) | 2026-02-20 | Marketing | $3,800 | 1 line item. `po_number: null`. |

> **CRITICAL: REQS.md amounts are truncated in some places.** Infer the full amounts from the line item sums shown in the labeled expectations. The `Invoice` model validator will catch mismatches.

> **CRITICAL: `unit_cost` MUST be set for equipment items.** INV-003 MacBook Pro: `unit_cost=1800.00`, `quantity=3`, `amount=5400.00`. The equipment classifier checks `unit_cost` to distinguish expense vs capitalize.

### 4.3 Unlabeled Invoices (10 total, UL-1 through UL-10)

Construct from REQS.md §Unlabeled Invoices. Same format as labeled invoices. PO numbers are truncated in REQS — assign reasonable PO numbers (e.g., `PO-2026-070` through `PO-2026-079`). Amounts are truncated — infer reasonable totals from context.

Key fixture details for unlabeled invoices:

| ID | Key Details |
|----|-------------|
| UL-1 | 2 lines. Line 1 "Annual Platform License" needs `service_period: {start: "2026-02-01", end: "2027-01-31"}`. Line 2 "Premium Support" is consulting, no period needed. |
| UL-6 | 2 lines: domain renewal + SSL certificate. Both need `service_period` (~1 year for annual renewal). |

All other unlabeled invoices: no service periods needed (monthly/one-time).

### 4.4 Purchase Orders

Create matching POs for every invoice that has a `po_number`. Set PO amounts to **match invoice totals exactly** (0% variance) for labeled invoices. For unlabeled invoices, set PO amounts to match as well (since we don't know the "real" PO amounts).

```json
[
    {"number": "PO-2026-044", "vendor": "Cloudware Solutions", "amount": "24000.00", "department": "Engineering"},
    {"number": "PO-2026-051", "vendor": "Morrison & Burke LLP", "amount": "9500.00", "department": "Legal"},
    ...
]
```

INV-006 has no PO — do NOT create a PO for it.

---

## 5. Fixture Loader (`src/fixtures.py`)

```python
import json
from pathlib import Path
from decimal import Decimal
from src.models import Invoice, PurchaseOrder

DATA_DIR = Path(__file__).parent.parent / "data"

def _decimal_hook(obj: dict) -> dict:
    """Convert string amounts to Decimal during JSON parsing."""
    # This is handled by Pydantic's Decimal coercion, so we can pass raw dicts.
    return obj

def load_invoices() -> list[Invoice]:
    """Load all invoices from data/invoices.json."""
    with open(DATA_DIR / "invoices.json") as f:
        data = json.load(f)
    return [Invoice.model_validate(inv) for inv in data]

def load_invoice(invoice_id: str) -> Invoice:
    """Load a single invoice by ID. Raises ValueError if not found."""
    for inv in load_invoices():
        if inv.id == invoice_id:
            return inv
    raise ValueError(f"Invoice {invoice_id} not found in fixtures")

def load_purchase_orders() -> dict[str, PurchaseOrder]:
    """Load POs as a lookup dict keyed by PO number."""
    with open(DATA_DIR / "purchase_orders.json") as f:
        data = json.load(f)
    return {po["number"]: PurchaseOrder.model_validate(po) for po in data}
```

---

## 6. SQLite Storage (`src/store.py`)

### 6.1 Schema

Three tables. Create these on first use (`init_db()`).

```sql
CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    lines_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_journal_entries_invoice ON journal_entries(invoice_id);

CREATE TABLE IF NOT EXISTS pending_approvals (
    invoice_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL,
    line_index INTEGER NOT NULL,
    original_gl TEXT,
    corrected_gl TEXT,
    original_treatment TEXT,
    corrected_treatment TEXT,
    reason TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 6.2 Store Class

```python
import sqlite3
import json
from pathlib import Path
from src.models import JournalEntry, ProcessingResult

DEFAULT_DB_PATH = Path(__file__).parent.parent / "gl.db"

class Store:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.init_db()

    def init_db(self):
        """Create tables if they don't exist."""
        # Execute the SQL schema above

    def has_journal_entries(self, invoice_id: str) -> bool:
        """Check if any journal entries exist for this invoice."""

    def has_pending(self, invoice_id: str) -> bool:
        """Check if a pending approval exists for this invoice."""

    def save_journal_entries(self, entries: list[JournalEntry]):
        """Insert all journal entries. Each entry is one row.
        lines_json = json.dumps([line.model_dump() for line in entry.lines])"""

    def save_pending(self, result: ProcessingResult):
        """Save ProcessingResult as JSON for HITL resume.
        Uses INSERT OR REPLACE to handle re-saves.
        result_json = result.model_dump_json()"""

    def load_pending(self, invoice_id: str) -> ProcessingResult:
        """Load a pending ProcessingResult. Raises ValueError if not found.
        ProcessingResult.model_validate_json(result_json)"""

    def delete_pending(self, invoice_id: str):
        """DELETE FROM pending_approvals WHERE invoice_id = ?"""

    def save_correction(self, invoice_id: str, line_index: int,
                        original_gl: str, corrected_gl: str,
                        original_treatment: str | None = None,
                        corrected_treatment: str | None = None,
                        reason: str | None = None):
        """Insert a correction row."""

    def list_corrections(self) -> list[dict]:
        """Return all corrections as dicts."""
```

### 6.3 Design Constraints

- **No SQLAlchemy.** Use `sqlite3` directly. Three tables, simple queries.
- **`lines_json` stores journal lines as JSON.** We never query individual debit/credit lines — always load full entries.
- **`result_json` stores the full serialized `ProcessingResult`.** Pydantic's `.model_dump_json()` / `.model_validate_json()` handles serialization.
- **`INSERT OR REPLACE` for pending_approvals** to handle re-saves if the same invoice is processed again.
- **All corrections go through SQLite only** — no separate `corrections.json` file.

---

## 7. Tests (`tests/test_models.py`)

Write tests that validate:

1. **Invoice validation:** `total_matches_lines` validator catches mismatched totals
2. **ServicePeriod validation:** `start > end` raises `ValueError`
3. **LineItem amount:** `amount=0` or negative amounts raise validation error
4. **JournalEntry.verify():** balanced entries return `True`, unbalanced return `False`
5. **Fixture loading:** all 16 invoices load and validate successfully
6. **PO loading:** all POs load and validate successfully
7. **Store round-trip:** save a JournalEntry → load it back, verify data integrity
8. **Store pending round-trip:** save a ProcessingResult → load it back, verify equality

Use `pytest`. Create a temporary SQLite DB for store tests (`tmp_path` fixture).

---

## 8. Acceptance Criteria

Phase 1 is done when:

- [ ] All models defined in `src/models.py` and importable
- [ ] `src/chart_of_accounts.py` has complete `GL_ACCOUNTS` dict, `PREPAID_TO_EXPENSE` mapping, and helper functions
- [ ] `data/invoices.json` has all 16 invoices (6 labeled + 10 unlabeled) with correct amounts, dates, departments, service periods, and unit_costs
- [ ] `data/purchase_orders.json` has all matching POs
- [ ] `src/fixtures.py` can load any invoice/PO
- [ ] `src/store.py` can init DB, save/load journal entries, save/load pending approvals, save/list corrections
- [ ] All tests in `tests/test_models.py` pass
- [ ] `uv run python -c "from src.models import Invoice; print('OK')"` works

---

## 9. What NOT to Build in Phase 1

- No pipeline logic (po_matcher, classifier, recognition, approver, poster)
- No engine orchestrator
- No CLI commands
- No eval system
- No `src/pipeline/` directory yet
