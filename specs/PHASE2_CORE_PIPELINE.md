# Phase 2 — Core Pipeline

> **Depends on:** Phase 1 (models, fixtures, chart of accounts, store)
> **Produces:** Steps 1–5 of the SOP pipeline, the engine orchestrator, and unit tests for each step.
> **Estimated effort:** ~5 hours

---

## Goal

Implement the full invoice processing pipeline: PO matching → GL classification → prepaid/accrual recognition → approval routing → journal entry generation & verification. After this phase, `Engine.process(invoice)` returns a correct `ProcessingResult` for all 6 labeled invoices.

---

## 1. Directory Structure

Create the `src/pipeline/` directory:

```
src/
├── pipeline/
│   ├── __init__.py
│   ├── po_matcher.py
│   ├── classifier.py
│   ├── recognition.py
│   ├── approver.py
│   └── poster.py
├── engine.py                    # Orchestrator
```

---

## 2. Step 1: PO Matcher (`src/pipeline/po_matcher.py`)

### Interface

```python
def match_po(invoice: Invoice, po_store: dict[str, PurchaseOrder]) -> POMatchResult:
```

A **pure function**. Takes an invoice and a PO lookup dict, returns a result. No side effects.

### Rules

| Condition | `status` | `matched` | Notes |
|-----------|----------|-----------|-------|
| `invoice.po_number is None` | `"no_po"` | `False` | Pipeline stops. Reason: "No PO number provided" |
| PO number not in `po_store` | `"po_not_found"` | `False` | Reason: f"PO {po_number} not found" |
| Amount variance > 10% | `"amount_mismatch"` | `False` | Reason includes the variance %. |
| Match | `"matched"` | `True` | Store PO reference and variance |

### Tolerance Calculation

```python
variance = abs(invoice.total - po.amount) / po.amount
if variance > Decimal("0.10"):  # Strict: >10% fails, exactly 10% passes
    return POMatchResult(status="amount_mismatch", matched=False, ...)
```

- Tolerance is **per-invoice total vs PO amount**, NOT per-line.
- 10% is exclusive: exactly 10.0% passes, >10.0% fails.
- Set `variance_pct` on the result for debugging.

### Tests (`tests/test_po_matcher.py`)

1. Invoice with matching PO (exact amount) → `matched=True`
2. Invoice with no `po_number` → `status="no_po"`
3. Invoice with PO number not in store → `status="po_not_found"`
4. Invoice 5% over PO amount → `matched=True` (within tolerance)
5. Invoice 15% over PO amount → `status="amount_mismatch"`
6. Invoice exactly 10% over → `matched=True` (boundary case)
7. Invoice 10.01% over → `matched=False`

---

## 3. Step 2: GL Classifier (`src/pipeline/classifier.py`)

### Architecture

**Priority-ordered rule chain.** Rules are defined as a list, evaluated in order. First match wins.

```python
from dataclasses import dataclass
from typing import Callable

@dataclass
class ClassificationRule:
    name: str
    priority: int
    fn: Callable[[LineItem, Invoice], Classification | None]

RULES: list[ClassificationRule] = [
    ClassificationRule(name="physical_goods",          priority=1, fn=classify_physical_goods),
    ClassificationRule(name="equipment",               priority=2, fn=classify_equipment),
    ClassificationRule(name="software_saas",           priority=3, fn=classify_software),
    ClassificationRule(name="cloud_hosting",            priority=4, fn=classify_cloud),
    ClassificationRule(name="professional_services",   priority=5, fn=classify_professional_services),
    ClassificationRule(name="marketing",               priority=6, fn=classify_marketing),
    ClassificationRule(name="other",                   priority=7, fn=classify_other),
]

def classify_line_item(item: LineItem, invoice: Invoice) -> Classification | None:
    """Apply rules in priority order. First match wins. Returns None for unclassifiable items."""
    for rule in RULES:
        result = rule.fn(item, invoice)
        if result is not None:
            return result
    return None
```

### Required Imports

```python
import re
from decimal import Decimal
from src.models import LineItem, Invoice, Classification, Treatment
from src.pipeline.recognition import resolve_service_period, is_annual
```

> **Note:** `resolve_service_period` and `is_annual` are imported from `recognition.py`. These functions are used by the classifier to detect annual vs monthly. Alternatively, you can define them as shared utilities and import from both places.

### Rule Implementations

Each rule function signature: `(item: LineItem, invoice: Invoice) -> Classification | None`

Every rule has an **explicit keyword guard** — no rule blindly returns a classification without first confirming the description matches its category.

#### 3.1 Physical Goods (Priority 1)

```python
def classify_physical_goods(item, invoice):
    keywords = ["office suppl", "stationery", "toner", "paper", "pen",
                "t-shirt", "swag", "gift bag", "branded", "merch"]
    if any(kw in item.description.lower() for kw in keywords):
        return Classification(gl_code="5000", treatment=Treatment.EXPENSE,
                              rule_applied="Physical goods → 5000")
    return None
```

- Branded merch → 5000 EVEN IF from Marketing department (INV-005 lines 2 and 4)
- This rule has the highest priority, so it catches physical goods before any other rule

#### 3.2 Equipment (Priority 2)

```python
def classify_equipment(item, invoice):
    substring_keywords = ["laptop", "macbook", "monitor", "printer",
                          "desktop", "workstation", "projector",
                          "scanner", "keyboard", "mouse", "headset"]
    regex_keywords = [r"\bserver\b(?! rental)", r"\bphone\b(?! plan)",
                      r"\bswitch\b", r"\brouter\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None
    unit_cost = item.unit_cost or item.amount
    if unit_cost >= 5000:
        return Classification(gl_code="1500", treatment=Treatment.CAPITALIZE,
                              rule_applied=f"Equipment unit cost ${unit_cost} ≥ $5K → 1500")
    return Classification(gl_code="5110", treatment=Treatment.EXPENSE,
                          rule_applied=f"Equipment unit cost ${unit_cost} < $5K → 5110")
```

- **`unit_cost` is the determining factor**, not total `amount`.
- 3× $1,800 laptops: `unit_cost=1800` → 5110 (expense). Total $5,400 is irrelevant.
- Dell server $8,500: `unit_cost=8500` → 1500 (capitalize).
- Word-boundary regex prevents "server" from matching "server rental" (which is cloud hosting at priority 4).
- Fallback: `unit_cost or item.amount` — if no unit_cost provided, uses total amount.

#### 3.3 Software/SaaS (Priority 3)

```python
def classify_software(item, invoice):
    substring_keywords = ["software", "saas", "subscription", "platform", "suite", "module",
                          "domain", "ssl", "certificate"]
    regex_keywords = [r"\bapp\b", r"\btool\b", r"\blicense\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1310", treatment=Treatment.PREPAID,
                              rule_applied="Software annual prepayment → 1310")
    return Classification(gl_code="5010", treatment=Treatment.EXPENSE,
                          rule_applied="Software monthly/usage → 5010")
```

- Annual/monthly detection uses `resolve_service_period()` (checks line-level then invoice-level).
- `is_annual()` returns True if `months_between(start, end) > 1`.
- INV-001 "Annual Platform License" with Jan–Dec period → 1310 (prepaid).
- UL-6 "Domain renewal" + "SSL certificate" → depends on service_period in fixture.

#### 3.4 Cloud Hosting (Priority 4)

```python
def classify_cloud(item, invoice):
    cloud_keywords = ["cloud", "hosting", "aws", "azure", "gcp",
                      "cloud infrastructure", "cloud storage", "object storage",
                      "compute", "cdn", "server rental", "reserved instance",
                      "api overage", "api usage"]
    if not any(kw in item.description.lower() for kw in cloud_keywords):
        return None
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1300", treatment=Treatment.PREPAID,
                              rule_applied="Cloud annual prepayment → 1300")
    return Classification(gl_code="5020", treatment=Treatment.EXPENSE,
                          rule_applied="Cloud monthly/usage → 5020")
```

- INV-003 "AWS Reserved Instances Annual" with Feb 2026–Jan 2027 → 1300 (prepaid).
- UL-9 "SMS API overage" → 5020 (monthly usage, via "api overage" keyword).

#### 3.5 Professional Services (Priority 5)

```python
def classify_professional_services(item, invoice):
    prof_service_keywords = ["legal", "law ", "attorney", "counsel", "consulting",
                             "advisory", "assessment", "litigation", "patent",
                             "prosecution", "placement", "recruiting", "staffing",
                             "contract review", "contract draft"]
    desc = item.description.lower()
    if not any(kw in desc for kw in prof_service_keywords):
        return None

    legal_action_keywords = ["litigation", "patent", "contract review", "prosecution",
                             "contract draft", "filing"]
    consulting_keywords = ["advisory", "consulting", "assessment", "analysis", "strategy",
                           "implementation", "optimization", "placement", "recruiting",
                           "staffing"]

    has_legal = any(kw in desc for kw in legal_action_keywords)
    has_consulting = any(kw in desc for kw in consulting_keywords)

    if has_legal and has_consulting:
        return Classification(gl_code="5030", treatment=Treatment.EXPENSE,
                              rule_applied="Mixed legal+consulting → 5030 (conservative)")
    if has_legal:
        return Classification(gl_code="5030", treatment=Treatment.EXPENSE,
                              rule_applied="Legal services → 5030")
    if has_consulting:
        return Classification(gl_code="5040", treatment=Treatment.EXPENSE,
                              rule_applied="Consulting/advisory → 5040")
    return None
```

**Key distinction:** "regulatory" as domain modifier ≠ legal action. "Regulatory compliance review & advisory" → advisory → 5040 (not 5030). See DESIGN.md §1b A1 for full rationale.

#### 3.6 Marketing (Priority 6)

```python
def classify_marketing(item, invoice):
    desc = item.description.lower()
    substring_keywords = ["advertising", "campaign", "sponsorship", "media buy",
                          "promotion", "seo", "ppc", "social media",
                          "booth", "exhibition", "brand identity", "redesign",
                          "creative", "design services"]
    regex_keywords = [r"\bad\b", r"\bads\b"]
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None
    return Classification(gl_code="5050", treatment=Treatment.EXPENSE,
                          rule_applied="Marketing/advertising → 5050")
```

- Conference booth → 5050 (marketing event). Conference registration → 5080 (training, priority 7).
- Uses word-boundary regex for "ad"/"ads" to avoid matching "advisory".

#### 3.7 Other Categories (Priority 7)

```python
def classify_other(item, invoice):
    desc = item.description.lower()
    other_rules = [
        (["travel", "flight", "hotel", "lodging", "airfare", "uber", "taxi", "meal",
          "per diem", "catering"], "5060", "Travel & entertainment → 5060"),
        (["maintenance", "facilities", "janitorial", "hvac", "plumbing", "repair",
          "renovation", "construction", "conversion", "remodel", "demolition",
          "coworking", "co-working", "hot desk", "workspace", "office rental",
          "desk membership"],
         "5070", "Facilities & maintenance → 5070"),
        (["training", "workshop", "certification", "course", "seminar",
          "conference registration"],
         "5080", "Training & development → 5080"),
        (["telecom", "internet", "phone plan", "mobile", "broadband", "sms"],
         "5090", "Telecom & internet → 5090"),
        (["insurance", "policy", "coverage", "premium"],
         "5100", "Insurance → 5100"),
    ]
    for keywords, gl_code, rule_name in other_rules:
        if any(kw in desc for kw in keywords):
            if gl_code == "5100" and is_annual(resolve_service_period(item, invoice)):
                return Classification(gl_code="1320", treatment=Treatment.PREPAID,
                                      rule_applied="Insurance annual prepayment → 1320")
            return Classification(gl_code=gl_code, treatment=Treatment.EXPENSE,
                                  rule_applied=rule_name)
    return None  # Unclassifiable
```

### Tests (`tests/test_classifier.py`)

Test each rule explicitly:

1. "office supplies" → 5000
2. "Branded company t-shirts" from Marketing dept → 5000 (not 5050)
3. "MacBook Pro" unit_cost=$1,800 → 5110
4. "Dell PowerEdge R760 Server" unit_cost=$8,500 → 1500
5. "Annual Platform License" with annual period → 1310
6. "Monthly SaaS subscription" no period → 5010
7. "Patent filing & prosecution" → 5030
8. "Regulatory compliance review & advisory" → 5040 (KEY TEST)
9. "Contract review for marketing partnership" → 5030
10. "AWS Reserved Instances Annual" with annual period → 1300
11. "SMS API overage" → 5020
12. "Q1 digital ad campaign management" → 5050
13. "Conference booth rental" → 5050
14. "Conference registration" → 5080
15. "Travel expenses" → 5060
16. Truly unclassifiable description → returns `None`
17. "server rental" → NOT equipment (negative lookahead test)

---

## 4. Step 3: Recognition Engine (`src/pipeline/recognition.py`)

### Shared Helpers

These are used by both the classifier (Phase 2) and the recognition engine:

```python
from datetime import date
from src.models import LineItem, Invoice, ServicePeriod

def resolve_service_period(item: LineItem, invoice: Invoice) -> ServicePeriod | None:
    """Line-level service_period takes priority; falls back to invoice-level."""
    return item.service_period or invoice.service_period

def is_annual(period: ServicePeriod | None) -> bool:
    """True if service period spans > 1 month. False for None (monthly/usage)."""
    if period is None:
        return False
    return months_between(period.start, period.end) > 1

def months_between(start: date, end: date) -> int:
    """Calendar months spanned (inclusive). Jan 1 – Dec 31 = 12."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1

def monthly_periods(period: ServicePeriod) -> list[date]:
    """First day of each calendar month in the period."""
    months = []
    current = period.start.replace(day=1)
    end_month = period.end.replace(day=1)
    while current <= end_month:
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months
```

### Main Entry Point

```python
def generate_entries(classified_line: ClassifiedLine, invoice: Invoice) -> list[JournalEntry]:
    classification = classified_line.classification
    item = classified_line.line_item
    period = resolve_service_period(item, invoice)

    if classification.treatment == Treatment.PREPAID:
        return generate_prepaid_entries(item, classification, invoice)

    # Accrual detection: service period ended STRICTLY BEFORE invoice date
    if period and period.end < invoice.date:
        classification.treatment = Treatment.ACCRUAL  # Override EXPENSE → ACCRUAL
        return generate_accrual_entries(item, classification, invoice)

    # Capitalize or expense (same journal entry shape)
    return generate_expense_entries(item, classification, invoice)
```

### Prepaid Entries

Generate initial booking + monthly amortization entries.

- **Initial:** Debit Prepaid account (e.g., 1310), Credit AP (2000)
- **Monthly amortization:** Debit Expense account (e.g., 5010), Credit Prepaid account (e.g., 1310)
- **Rounding:** `ROUND_HALF_UP`, last month gets remainder
- **Amortization dates:** Use `monthly_periods()` — each entry dated first of its month
- **Do NOT set `posting_gl`** for prepaids — `gl_code` already IS the prepaid account

Use `PREPAID_TO_EXPENSE` mapping from `chart_of_accounts.py` to find the expense account.

### Expense/Capitalize Entries

Simple AP entry: Debit GL account, Credit AP (2000). Same shape for both expense and capitalize — only the GL code differs.

### Accrual Entries

Generate two journal entries per accrual line:

1. **Accrual entry** (dated `service_period.end`): Debit expense GL, Credit accrual liability (2110 for professional services, 2100 for all others)
2. **Reversal entry** (dated `invoice.date`): Debit accrual liability, Credit AP (2000)

**Set `classification.posting_gl` to the accrual account** — this is what the eval compares.

Use `is_professional_service(gl_code)` from `chart_of_accounts.py` to determine which accrual account.

### Shadow Mode Warning

When `0 < (invoice.date - period.end).days <= 7`, the engine should add a warning. However, this warning is generated in `engine.py` (the orchestrator), not in recognition.py. The recognition engine only produces journal entries; the engine adds the warning to `ProcessingResult.warnings`.

### Tests (`tests/test_recognition.py`)

1. **months_between:** Jan 1–Dec 31 → 12. Jan 1–Jan 31 → 1. Feb 1–Jan 31 → 12.
2. **is_annual:** 12-month period → True. 1-month → False. None → False.
3. **monthly_periods:** Jan 1–Dec 31 → 12 dates starting Jan 1.
4. **Prepaid entries:** $24,000 / 12 months → 1 initial entry + 12 amortization entries. Sum of amortization = $24,000.
5. **Non-clean amortization:** $25,000 / 12 → verify remainder handling (11 × $2,083.33 + $2,083.37 = $25,000).
6. **Accrual entries:** Service period Dec 2025, invoice date Jan 2026 → 2 entries (accrual + reversal). Classification.treatment mutated to ACCRUAL, posting_gl set.
7. **Expense entries:** Simple line item → 1 entry (debit GL, credit AP).
8. **Insurance annual prepaid:** gl_code=1320 → amortizes to 5100.
9. **Service period resolution:** Line-level overrides invoice-level.

---

## 5. Step 4: Approval Router (`src/pipeline/approver.py`)

### Interface

```python
def route_approval(invoice: Invoice, classifications: list[ClassifiedLine]) -> ApprovalDecision:
```

### Evaluation Order

1. **Fixed Asset check:** if any line classified as `1500` → `VP_FINANCE` regardless of amount
2. **Department overrides:**
   - Marketing: auto-approve if `invoice.total <= 2500`
   - Engineering: auto-approve if `invoice.total <= 5000` AND all lines' `gl_code` is `5020` or `5010`
3. **Base thresholds:**
   - `total <= 1000` → `AUTO`
   - `1000 < total <= 10000` → `DEPT_MANAGER`
   - `total > 10000` → `VP_FINANCE`
4. **Default:** `DENIED` (fail-closed)

### Key Details

- **Override semantics:** Overrides can only widen auto-approve, never narrow. If the override doesn't match (e.g., Engineering but not all cloud/software), fall through to base thresholds.
- **Engineering override checks `gl_code`**, not the underlying expense. Prepaid codes (1310/1300) do NOT satisfy the override — they are not 5020/5010. Annual prepaid invoices from Engineering fall through to base thresholds.
- Comparison thresholds: ≤$1K is `total <= Decimal("1000")`. $1K–$10K is `Decimal("1000") < total <= Decimal("10000")`. >$10K is `total > Decimal("10000")`.

### Tests (`tests/test_approver.py`)

1. Invoice $500, any dept → AUTO
2. Invoice $5,000, Operations dept → DEPT_MANAGER
3. Invoice $15,000, any dept → VP_FINANCE
4. Invoice with any line 1500 → VP_FINANCE regardless of amount
5. Marketing invoice $2,000 → AUTO (override)
6. Marketing invoice $8,000 → DEPT_MANAGER (override doesn't apply, falls to base)
7. Engineering invoice $4,000, all lines 5020/5010 → AUTO (override)
8. Engineering invoice $4,000, mixed lines (5020 + 5110) → DEPT_MANAGER (override fails)
9. Engineering invoice $4,000, line with 1310 (prepaid software) → DEPT_MANAGER (override fails, not 5010)

---

## 6. Step 5: Journal Poster (`src/pipeline/poster.py`)

### Verification

```python
def verify_journal_entries(entries: list[JournalEntry], invoice_total: Decimal) -> bool:
    # 1. Each entry must balance internally
    for entry in entries:
        if not entry.verify():
            return False
    # 2. Total AP (2000) credits across ALL entries must equal invoice total
    ap_credits = sum(
        l.credit for e in entries for l in e.lines
        if l.account_code == "2000"
    )
    return ap_credits == invoice_total
```

**Why AP credits:** The AP control account (2000) is the universal invariant. Every invoice credits AP for its full amount regardless of treatment path. Amortization entries don't touch AP. Accrual entries don't touch AP in the accrual phase, but the reversal does.

### Tests (`tests/test_poster.py`)

1. Balanced simple expense entries → verification passes
2. Unbalanced entries → verification fails
3. Prepaid entries (1 initial + 12 amortization) → AP credits = invoice total
4. Accrual entries (accrual + reversal) → AP credits = invoice total
5. Mixed invoice (INV-003: 3 line types) → AP credits = total

---

## 7. Engine Orchestrator (`src/engine.py`)

### Class Design

```python
class Engine:
    def __init__(self, store: Store, po_store: dict[str, PurchaseOrder]):
        self.store = store
        self.po_store = po_store

    def process(self, invoice: Invoice, *, dry_run=False, shadow=False) -> ProcessingResult:
        ...

    def resume(self, invoice_id: str, approved: bool) -> ProcessingResult:
        ...
```

### `process()` Flow

1. **Idempotency check** (skip for dry_run/shadow): error if already posted or pending
2. **Step 1:** PO matching — if not matched, set `status="flagged"`, return early
3. **Step 2:** Classify each line item — if ANY are `None` (unclassifiable), set `status="flagged"`, return early
4. **Step 3:** Generate journal entries via recognition engine — for each classified line
5. **Step 4:** Route approval
6. **Step 5:** Verify journal entries balance
7. **Mode-specific exits:**
   - Dry-run/shadow: set status, return (always return proposals, even with warnings)
   - Verification failed in normal mode: `status="error"`, return
   - Denied: save to pending (allow HITL override), `status="flagged"`, return
   - Needs approval: save to pending, `status="pending_approval"`, return
   - Auto-approved: save journal entries, `status="posted"`, return

### `resume()` Flow

1. Load pending ProcessingResult from store
2. If approved: re-verify entries, save to journal_entries, `status="posted"`
3. If rejected: `status="rejected"`
4. Delete from pending_approvals in both cases

### Key Design Notes

- `dry_run` and `shadow` are boolean flags, not separate code paths
- **Denied invoices are NOT dead ends** — saved to pending, can be resumed
- Pipeline is linear, not a DAG. No async needed.
- The engine injects `Store` and `po_store` as dependencies

---

## 8. Acceptance Criteria

Phase 2 is done when:

- [ ] Each pipeline step has its own module in `src/pipeline/`
- [ ] `Engine.process(inv, dry_run=True)` returns correct `ProcessingResult` for all 6 labeled invoices
- [ ] Unit tests pass for po_matcher, classifier, recognition, approver, poster
- [ ] INV-001: 1310 prepaid + 12 amortization entries
- [ ] INV-002: 5030/5040/5030 classification
- [ ] INV-003: 5110 + 1500 + 1300 (mixed invoice)
- [ ] INV-004: 2110/2100 accrual entries with reversals
- [ ] INV-005: 5050/5000/5050/5000 (branded merch exception)
- [ ] INV-006: Flagged (no PO)
- [ ] All journal entries verify (debits = credits, AP = total)

---

## 9. What NOT to Build in Phase 2

- No CLI commands (Phase 4)
- No eval system, ground truth, or metrics (Phase 3)
- No shadow mode output formatting (Phase 3)
- No feedback/correction storage logic beyond `store.save_correction()` (Phase 3)
- No HITL resume command or state-file management (Phase 4)
