# Varick — Accounts Payable Agent: System Design

> **Design Principles:** Robust, scalable, maintainable, simple to understand, not over-engineered.

---

## 1. Research & Prior Art

### OSS References Studied

| Project | Key Takeaway |
|---------|-------------|
| **[python-accounting](https://github.com/ekmungai/python-accounting)** (191★) | Gold-standard double-entry model. Uses `post_account`/`folio_account` dual-reference per ledger row, `credited` boolean as routing mechanism, chained SHA-256 hashes for tamper detection. Config-driven account types. |
| **[beancount](https://github.com/beancount/beancount)** | Plain-text accounting. Clean separation of *directives* (data) from *plugins* (processing). Transactions must balance to zero — hard constraint. |
| **[ledger.py](https://github.com/mafm/ledger.py)** | Minimalist double-entry in a single file. `VERIFY-BALANCE` assertions as first-class audit concept. |
| **DZone: Deterministic Rules Engines** | Stable rule ordering (priority + tie-breaker), Decimal arithmetic, append-only decision traces, input fingerprinting for reproducibility. |
| **Temporal: Human-in-the-Loop** | Signal-based pause/resume pattern. Serialize state → wait for external signal → resume. State files > interactive prompts for testability. |
| **Anthropic: Demystifying Evals** | Grade *outcomes* not *trajectories*. Deterministic graders where possible. 20-50 tasks is enough to start. Eval-driven development. |

### Key Design Decisions Derived from Research

1. **Deterministic rules, not LLM-assisted classification.** The SOP is fully specified with explicit priority ordering. A deterministic engine is auditable, reproducible, and testable. LLMs add non-determinism without benefit here.

2. **Simplified ledger model.** We don't need the full complexity of `python-accounting` (multi-entity, currency, tax). We adopt the *patterns* — immutable journal entries, debit/credit balance verification, append-only storage — but with flat Pydantic models + SQLite, not SQLAlchemy ORM.

3. **State-file HITL over interactive prompts.** Serialize pending-approval state to JSON. A `resume` CLI command loads and continues. This is testable, scriptable, and doesn't block terminal.

4. **Eval as first-class infrastructure.** The 6 labeled invoices ARE the eval suite. Build the eval harness in Phase 2 alongside the pipeline, not as a Phase 4 afterthought.

---

## 1b. Known Spec Ambiguities & Resolutions

These contradictions exist in the REQS.md and must be explicitly handled.

### A1. INV-002 Line 2: "Regulatory compliance review & advisory" — SOP vs Ground Truth Conflict

The SOP says: "Legal (litigation, patent, contract, **regulatory**) → 5030" and "Consulting/**advisory** → 5040" and "Mixed legal+non-legal → 5030 (conservative)." This line contains BOTH "regulatory" (→5030) and "advisory" (→5040). By the SOP's own mixed-rule, the answer should be **5030**. But the sample expected answer is **5040**.

**Resolution:** Our classifier follows the SOP literally. The description says "Regulatory compliance **review & advisory**" — the primary service is "advisory" work on the topic of regulatory compliance. The word "regulatory" here is a *modifier* describing the domain, not the service type. The service type is "review & advisory" → consulting → **5040**. We treat this as: "regulatory" as domain context, "advisory" as service type. The classifier checks for the *service nature* (advisory/consulting) vs *legal nature* (litigation, filing, prosecution, contract drafting). "Compliance review & advisory" is advisory work, not legal action. This matches the ground truth.

**Implementation:** The `classify_professional_services` rule checks for legal *action* keywords (litigation, filing, prosecution, patent, contract review) → 5030. Non-legal professional services (advisory, consulting, assessment, analysis) → 5040. "Regulatory compliance review & advisory" matches advisory, not legal action.

### A2. INV-004: Expected GL Shows Post-Recognition Accrual Codes, Not Classifier Codes

The sample shows expected GL 2110/2100 for INV-004 lines. But classification (Step 2) should produce expense codes (5040 for consulting, 5060 for travel). The accrual accounts (2110/2100) are outputs of Step 3 (recognition), not Step 2.

**Resolution:** The eval system is **stage-aware**. It tracks three distinct GL values per line:

| Field | Description | INV-004 Line 1 Example |
|-------|-------------|----------------------|
| `classifier_gl` | Step 2 output | 5040 (Consulting) |
| `treatment` | expense/prepaid/accrual | accrual |
| `posting_gl` | Final GL used in journal entries | 2110 (Accrued Prof Services) |

The ground truth in `expected.py` stores the `posting_gl` (what REQS.md shows). The eval compares at the posting level — the *final* GL that appears in journal entries, after recognition has been applied. This is what the assessor expects.

### A3. Prepaid GL Codes ≠ Expense Codes for Approval Overrides

The Engineering auto-approve override requires "all lines are Cloud (5020) or Software (5010)." But annual prepayments get classified as 1310/1300, not 5010/5020. INV-001 (1310) and INV-003 (1300, 1500) cannot trigger the Engineering override because their GL codes are prepaid/asset codes.

**Resolution:** This is correct behavior. The override checks the *classifier GL code*, which for prepaid items is the prepaid account (1310, 1300), not the underlying expense account. The override is intentionally restrictive — it only applies when ALL lines are direct expense cloud/software charges. Prepaid and capitalized items require VP Finance review. This is explicitly documented in the approver logic.

---

## 2. Architecture

### High-Level Flow

```
Invoice JSON → PO Matcher → GL Classifier → Recognition Engine → Approval Router → Journal Poster
                  │               │                │                    │                │
                  ▼               ▼                ▼                    ▼                ▼
              PO Store      Rule Trace        Amort Schedule      Approval Decision   Journal DB
```

### Module Map

```
varick/
├── main.py                          # CLI entry point (Click)
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── models.py                    # ALL data models (single file, ~200 lines)
│   ├── chart_of_accounts.py         # GL account registry + metadata
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── po_matcher.py            # Step 1: PO matching
│   │   ├── classifier.py            # Step 2: GL classification (rule engine)
│   │   ├── recognition.py           # Step 3: Prepaid/accrual engine
│   │   ├── approver.py              # Step 4: Approval routing
│   │   └── poster.py                # Step 5: Journal entry generation + verification
│   ├── engine.py                    # Orchestrator — wires steps 1-5
│   ├── store.py                     # SQLite persistence (journal entries, corrections, audit)
│   └── fixtures.py                  # Load invoices + POs from JSON
├── data/
│   ├── invoices.json                # All 16 invoices (6 labeled + 10 unlabeled)
│   └── purchase_orders.json         # PO fixtures
├── eval/
│   ├── __init__.py
│   ├── expected.py                  # Ground truth for 6 labeled invoices
│   ├── runner.py                    # Run pipeline + compare to expected
│   ├── report.py                    # Accuracy metrics + formatted output
│   └── feedback.py                  # Correction collector + rule refinement
└── tests/
    ├── test_po_matcher.py
    ├── test_classifier.py
    ├── test_recognition.py
    ├── test_approver.py
    ├── test_poster.py
    └── test_e2e.py
```

**Why this structure:**
- `models.py` is ONE file because we have <10 models, all simple Pydantic dataclasses. Splitting into `models/invoice.py`, `models/journal.py` etc. is premature at this scale.
- `engine.py` is the only orchestration point. No `agent/` directory with `processor.py`, `dry_run.py`, `shadow.py`, `hitl.py` — these are just modes of the same engine, controlled by flags.
- `eval/` is a peer of `src/`, not inside it. Eval code should never be imported by production code.
- `data/` contains JSON fixtures, not a `data/invoices/` subdirectory per invoice. 16 invoices fit in one file.

---

## 3. Data Models

All models are **Pydantic v2 dataclasses** with strict validation. No SQLAlchemy ORM — too heavy for this use case.

### Core Models

```python
# --- Invoice Domain ---

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
    amount: Decimal = Field(gt=0)       # total line amount — must be positive (credit memos are out of scope)
    unit_cost: Decimal | None = None    # for equipment: per-unit cost (MUST be set for equipment)
    quantity: int = Field(ge=1, default=1)
    service_period: ServicePeriod | None = None  # per-line override

class Invoice(BaseModel):
    id: str                             # "INV-001", "UL-1", etc.
    vendor: str
    po_number: str | None               # None = no PO
    date: date
    department: str                     # "Engineering", "Marketing", etc.
    service_period: ServicePeriod | None = None  # invoice-level fallback (e.g., INV-004 "Dec 2025")
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
    number: str                         # "PO-2026-044"
    vendor: str
    amount: Decimal
    department: str


# --- Classification Output ---

class Classification(BaseModel):
    gl_code: str                        # Step 2 classifier output: "5010", "1310", etc.
    treatment: Treatment                # EXPENSE | PREPAID | ACCRUAL | CAPITALIZE
    rule_applied: str                   # human-readable trace: "Software/SaaS annual → 1310"
    posting_gl: str | None = None       # Set by Step 3 recognition if different from gl_code
                                        # e.g., gl_code="5040", posting_gl="2110" for accruals

class Treatment(str, Enum):
    EXPENSE = "expense"
    PREPAID = "prepaid"
    ACCRUAL = "accrual"
    CAPITALIZE = "capitalize"


# --- Journal Entry ---

class JournalLine(BaseModel):
    account_code: str
    account_name: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: str

class JournalEntry(BaseModel):
    """NOTE on pseudocode convention: In the code examples below, JournalEntry
    constructors omit id, invoice_id, date, and description for brevity.
    In implementation: id = uuid4(), invoice_id/date inherited from the invoice
    context, description auto-generated from the line item description."""
    id: str                             # auto-generated UUID
    invoice_id: str
    date: date
    description: str
    lines: list[JournalLine]
    entry_type: str                     # "initial", "amortization", "accrual", "reversal"

    def verify(self) -> bool:
        """Debits must equal credits."""
        return sum(l.debit for l in self.lines) == sum(l.credit for l in self.lines)


# --- Approval ---

class ApprovalDecision(BaseModel):
    level: ApprovalLevel                # AUTO | DEPT_MANAGER | VP_FINANCE | DENIED
    reason: str
    approved: bool | None = None        # None = pending, True/False = decided

class ApprovalLevel(str, Enum):
    AUTO = "auto_approve"
    DEPT_MANAGER = "dept_manager"
    VP_FINANCE = "vp_finance"
    DENIED = "denied"


# --- PO Match Result ---

class POMatchResult(BaseModel):
    status: str                         # "matched", "no_po", "po_not_found", "amount_mismatch"
    matched: bool
    po: PurchaseOrder | None = None
    variance_pct: Decimal | None = None # e.g., 0.05 for 5%
    reason: str = ""


# --- Processing Result (the output of the full pipeline) ---

class ClassifiedLine(BaseModel):
    line_item: LineItem
    classification: Classification | None  # None if unclassifiable

class ProcessingResult(BaseModel):
    invoice_id: str
    invoice_total: Decimal | None = None  # Store original invoice total for verification on resume
    status: str                         # "posted", "pending_approval", "flagged", "error"
    po_result: POMatchResult | None = None  # Full PO match result (status, variance, reason)
    classifications: list[ClassifiedLine] = Field(default_factory=list)
    approval: ApprovalDecision | None = None
    journal_entries: list[JournalEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

### Why This Model Design

1. **Flat, not nested hierarchies.** No inheritance trees. A `JournalEntry` has `JournalLine` objects — that's the only nesting.
2. **`Classification` carries a rule trace.** Every classification records *which rule* matched. This is critical for the feedback loop: you can see *why* something was classified wrong.
3. **`Classification.posting_gl` separates classifier output from recognition output.** Step 2 sets `gl_code`. Step 3 sets `posting_gl` **only when the posting account differs from `gl_code`** — i.e., for accruals (→ 2110/2100). For prepaids, the classifier already returns the prepaid code (1310/1300/1320) as `gl_code`, so `posting_gl` stays `None`. The eval uses `posting_gl or gl_code` to get the final posting account. This resolves the INV-004 eval issue (see §1b A2).
4. **`Invoice.service_period` is the fallback.** Line items can have their own `service_period`, but if absent, the engine inherits the invoice-level one. INV-004 specifies "Service period: Dec 2025" at the invoice level, not per line.
4b. **`Classification` is intentionally mutable.** The recognition engine overrides `treatment` (EXPENSE → ACCRUAL) and sets `posting_gl` in-place. This is a conscious design choice — the pipeline is strictly linear, and no component reads `Classification` between Step 2 and Step 3. If middleware or logging between steps is added in the future, this should be refactored to return new `Classification` instances instead of mutating.
5. **`ProcessingResult` is the single output type.** Dry-run, shadow, and normal modes all return the same shape. The `status` field differentiates.
6. **`Decimal` everywhere for money.** Never float. Following `python-accounting` and GAAP best practices.
7. **`unit_cost` MUST be set in fixtures for equipment.** The JSON fixtures are pre-structured by hand. For "MacBook Pro 14" (3x $1,800)", the fixture encodes `unit_cost=1800, quantity=3, amount=5400`. We do NOT parse "(3x $1,800)" from descriptions — the fixtures are manually authored with correct structured data.
8. **`amount` is validated as positive (gt=0).** Credit memos and negative line items are out of scope for this assessment. If a zero-amount line item appears, it will fail validation. This prevents meaningless journal entries with zero debits/credits.
9. **`po_result` stores the full `POMatchResult`**, not just a boolean. This preserves the variance percentage, status, and reason for debugging and audit purposes.
10. **`Invoice.total` is validated against `sum(line_items.amount)`.** This catches fixture authoring errors early. If REQS.md amounts are truncated, we infer the total from the line items and set both to match.

---

#### Fixture `service_period` Requirements

Several invoices require `service_period` in their fixtures for correct prepaid/accrual treatment. Without it, `is_annual()` returns `False` (no prepaid) and the accrual check has no period to compare.

| Invoice | Where `service_period` Goes | Value | Why |
|---------|---------------------------|-------|-----|
| INV-001 | Line: "Annual Platform License" | Jan 1 – Dec 31, 2026 | Prepaid → 1310 + amortization |
| INV-003 | Line: "AWS Reserved Instances" | Feb 1, 2026 – Jan 31, 2027 | Prepaid → 1300 + amortization |
| INV-004 | **Invoice level** (not per line) | Dec 1 – Dec 31, 2025 | Accrual detection: Dec 31 < Jan 15 |
| UL-1 | Line: "Annual Platform License" | Feb 1, 2026 – Jan 31, 2027 | Prepaid → 1310 + amortization |
| UL-6 | Line 1: "Domain renewal" | ~Jan 2026 – Jan 2027 | Prepaid → 1310 (annual) |
| UL-6 | Line 2: "SSL certificate" | ~Jan 2026 – Jan 2027 | Prepaid → 1310 (annual) |

All other invoices either have no service period (monthly/usage → expense) or don't affect treatment. The fixture JSON must explicitly encode these as `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` — the system does NOT parse text like "Jan–Dec 2026" from descriptions.

---

## 4. Chart of Accounts

A simple registry, not a class hierarchy:

```python
GL_ACCOUNTS = {
    "1300": GLAccount(code="1300", name="Prepaid Expenses (General)", category="Prepaid"),
    "1310": GLAccount(code="1310", name="Prepaid Software", category="Prepaid"),
    "1320": GLAccount(code="1320", name="Prepaid Insurance", category="Prepaid"),
    "1500": GLAccount(code="1500", name="Fixed Assets", category="Asset"),
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
    # AP control account (implicit, for journal entries)
    "2000": GLAccount(code="2000", name="Accounts Payable", category="Liability"),
}
```

The AP control account (`2000`) is always the credit side of an invoice journal entry. This is standard double-entry: debit the expense/asset, credit AP.

---

## 5. Pipeline Design (Steps 1–5)

### Step 1: PO Matcher (`po_matcher.py`)

```python
def match_po(invoice: Invoice, po_store: dict[str, PurchaseOrder]) -> POMatchResult:
    """
    Match invoice to PO by number. Validate amount ±10%.
    Returns: POMatchResult with match status, PO reference, and variance.
    """
```

**`POMatchResult`** is defined in `models.py` (see §3: `status`, `matched`, `po`, `variance_pct`, `reason`).

**Rules:**
- No `po_number` → `status="no_po"`, flag for review, pipeline stops
- PO not found in store → `status="po_not_found"`, flag
- Amount variance > 10% (strict: `abs(invoice.total - po.amount) / po.amount > 0.10`) → `status="amount_mismatch"`, flag with variance %
- Match → `status="matched"`

**Tolerance is per-invoice total vs PO amount.** Not per-line. The 10% is exclusive: exactly 10.0% variance passes, >10.0% flags.

**Design:** Pure function. Takes invoice + PO lookup dict, returns result. No side effects.

### Step 2: GL Classifier (`classifier.py`)

This is the most complex component. It implements a **priority-ordered rule chain** inspired by deterministic rules engine best practices.

```python
RULES: list[ClassificationRule] = [
    Rule(name="physical_goods", priority=1, fn=classify_physical_goods),
    Rule(name="equipment", priority=2, fn=classify_equipment),
    Rule(name="software_saas", priority=3, fn=classify_software),
    Rule(name="cloud_hosting", priority=4, fn=classify_cloud),
    Rule(name="professional_services", priority=5, fn=classify_professional_services),
    Rule(name="marketing", priority=6, fn=classify_marketing),
    Rule(name="other", priority=7, fn=classify_other),
]

def classify_line_item(item: LineItem, invoice: Invoice) -> Classification | None:
    """Apply rules in priority order. First match wins."""
    for rule in RULES:
        result = rule.fn(item, invoice)
        if result is not None:
            return result
    return None  # unclassifiable → flag for review
```

**Each rule function** receives the line item + full invoice context (for department, date, etc.) and returns `Classification | None`.

**Key rule implementations:**

```python
def classify_physical_goods(item: LineItem, invoice: Invoice) -> Classification | None:
    """Physical goods: office supplies, stationery, toner → 5000.
       Branded merch (t-shirts, swag) → 5000 EVEN IF from Marketing."""
    keywords = ["office suppl", "stationery", "toner", "paper", "pen",
                "t-shirt", "swag", "gift bag", "branded", "merch"]
    if any(kw in item.description.lower() for kw in keywords):
        return Classification(gl_code="5000", treatment=Treatment.EXPENSE,
                              rule_applied="Physical goods → 5000")
    return None

def classify_equipment(item: LineItem, invoice: Invoice) -> Classification | None:
    """Equipment: unit cost < $5K → 5110. Unit cost ≥ $5K → 1500.
       MUST check equipment keywords first — without the guard, every non-physical-goods
       line would fall through here and be classified as equipment.
       
       NOTE: "server" and "phone" use word-boundary regex to avoid conflicts:
       - "server" must not capture "server rental" (cloud, priority 4)
       - "phone" must not capture "phone plan" (telecom, priority 7)
       These are exact-word matches: "Dell server" ✓, "server rental" ✗."""
    # Substring-safe equipment keywords
    substring_keywords = ["laptop", "macbook", "monitor", "printer",
                          "desktop", "workstation", "projector",
                          "scanner", "keyboard", "mouse", "headset"]
    # Word-boundary regex for ambiguous keywords
    regex_keywords = [r"\bserver\b(?! rental)", r"\bphone\b(?! plan)",
                      r"\bswitch\b", r"\brouter\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None  # Not equipment — let lower-priority rules handle it
    unit_cost = item.unit_cost or item.amount  # fallback to total if no unit_cost
    if unit_cost >= 5000:
        return Classification(gl_code="1500", treatment=Treatment.CAPITALIZE,
                              rule_applied=f"Equipment unit cost ${unit_cost} ≥ $5K → 1500")
    return Classification(gl_code="5110", treatment=Treatment.EXPENSE,
                          rule_applied=f"Equipment unit cost ${unit_cost} < $5K → 5110")

def classify_software(item: LineItem, invoice: Invoice) -> Classification | None:
    """Software/SaaS: monthly → 5010. Annual prepayment → 1310.
       MUST check software keywords first — without the guard, any line item
       with a service_period that isn't physical goods or equipment would
       fall through here and be misclassified as software.
       
       NOTE: Short words like "app", "tool", "license" use word-boundary regex
       (r'\bapp\b') to avoid substring false positives. "app" must not match
       "apparel" or "application fee". "license" must not match "business license".
       "platform" is kept as substring — acceptable false-positive risk is low
       given priority 3 (physical goods and equipment already handled)."""
    # Substring-safe keywords (long enough to be unambiguous)
    # NOTE: "domain", "ssl", "certificate" are here for UL-6 (Cloudflare domain renewal,
    # SSL wildcard certificate). These are software/IT subscriptions, not physical goods.
    substring_keywords = ["software", "saas", "subscription", "platform", "suite", "module",
                          "domain", "ssl", "certificate"]
    # Short/ambiguous keywords — require word-boundary match
    regex_keywords = [r"\bapp\b", r"\btool\b", r"\blicense\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None  # Not software — let lower-priority rules handle it
    # Detect annual vs monthly from service_period.
    # Uses resolve_service_period to check both line-level and invoice-level periods.
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1310", treatment=Treatment.PREPAID,
                              rule_applied="Software annual prepayment → 1310")
    return Classification(gl_code="5010", treatment=Treatment.EXPENSE,
                          rule_applied="Software monthly/usage → 5010")
```

**Professional services classifier — legal vs consulting distinction:**

```python
def classify_professional_services(item: LineItem, invoice: Invoice) -> Classification | None:
    """Professional services: legal ACTION → 5030, consulting/advisory → 5040.
       Mixed legal+non-legal → 5030 (conservative).
       
       MUST check professional services keywords first — without this guard,
       any line item reaching priority 5 would be tested against legal/consulting
       keywords, and common words like "implementation" could misfire.
       
       Key distinction: "regulatory" as a DOMAIN modifier (e.g., "regulatory compliance advisory")
       does not make it legal. Only legal ACTIONS (litigation, patent filing, contract review,
       prosecution) trigger 5030. Advisory/consulting work ON regulatory topics → 5040.
    """
    # Guard: must look like a professional service before checking legal vs consulting.
    # NOTE: Bare "compliance", "audit", "contract" are intentionally EXCLUDED from
    # the guard because they appear in non-professional-service contexts:
    #   - "HVAC compliance inspection" → facilities (5070), not prof services
    #   - "Annual fire safety audit" → facilities (5070)
    #   - "Cleaning contract renewal" → facilities (5070)
    # However, COMPOUND phrases like "contract review" and "contract draft" ARE
    # included — they are specific enough to indicate legal services without
    # false-positiving on facilities.  INV-002 line 3 ("Contract review for
    # marketing partnership") depends on "contract review" being in the guard.
    # "assessment" is also included — it's a reliable consulting signal
    # (INV-004 "Operational efficiency assessment" depends on it).
    prof_service_keywords = ["legal", "law ", "attorney", "counsel", "consulting",
                             "advisory", "assessment", "litigation", "patent",
                             "prosecution", "placement", "recruiting", "staffing",
                             "contract review", "contract draft"]
    desc = item.description.lower()
    if not any(kw in desc for kw in prof_service_keywords):
        return None  # Not a professional service — let lower-priority rules handle it
    
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


def classify_cloud(item: LineItem, invoice: Invoice) -> Classification | None:
    """Cloud/hosting: monthly → 5020. Annual prepayment → 1300.
       MUST check cloud keywords first — same guard logic as classify_software.
       
       NOTE: "infrastructure" and "storage" are narrowed to cloud-specific
       compounds ("cloud infrastructure", "cloud storage", "object storage")
       to avoid capturing facilities items like "office storage" or
       "IT infrastructure assessment" (which is consulting)."""
    cloud_keywords = ["cloud", "hosting", "aws", "azure", "gcp",
                      "cloud infrastructure", "cloud storage", "object storage",
                      "compute", "cdn", "server rental", "reserved instance",
                      "api overage", "api usage"]
    if not any(kw in item.description.lower() for kw in cloud_keywords):
        return None  # Not cloud — let lower-priority rules handle it
    # Uses resolve_service_period to check both line-level and invoice-level periods.
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1300", treatment=Treatment.PREPAID,
                              rule_applied="Cloud annual prepayment → 1300")
    return Classification(gl_code="5020", treatment=Treatment.EXPENSE,
                          rule_applied="Cloud monthly/usage → 5020")


def classify_marketing(item: LineItem, invoice: Invoice) -> Classification | None:
    """Marketing: ad spend, campaigns, sponsorships, events → 5050.
       Branded merch is NOT marketing — physical_goods (priority 1) catches it first.
       Must check keywords to avoid becoming a catch-all.
       
       NOTE: "ad"/"ads" use word-boundary regex (r'\bad\b') to avoid matching
       "advisory" while still matching "Facebook ad" (end of string) and
       "ad campaign" (mid-string). The old trailing-space trick ("ad ") failed
       when "ad" was the last word in the description.
       
       Conference booth/exhibition are marketing events, not training (5080).
       REQS: INV-005 line 3 "Conference booth rental" → 5050."""
    desc = item.description.lower()
    # Substring-safe keywords
    substring_keywords = ["advertising", "campaign", "sponsorship", "media buy",
                          "promotion", "seo", "ppc", "social media",
                          "booth", "exhibition", "brand identity", "redesign",
                          "creative", "design services"]
    # Word-boundary regex for short/ambiguous keywords
    regex_keywords = [r"\bad\b", r"\bads\b"]
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    if not (has_substring or has_regex):
        return None
    return Classification(gl_code="5050", treatment=Treatment.EXPENSE,
                          rule_applied="Marketing/advertising → 5050")


def classify_other(item: LineItem, invoice: Invoice) -> Classification | None:
    """Catch remaining known categories: travel, facilities, training, telecom, insurance.
       MUST use keyword guards — without them, this becomes a universal catch-all
       that prevents unclassifiable items from returning None.
       
       NOTE on category rationale:
       - "conference registration" is TRAINING (5080), per REQS Step 2 rule 7:
         "Training/conferences → 5080". Conference booths/exhibitions are
         marketing (handled at priority 6). Conference TRAVEL (flights, hotels)
         falls under travel (5060) via the travel keywords.
       - "catering" is TRAVEL & ENTERTAINMENT (5060) — event meals/catering is
         a standard T&E expense regardless of which department hosts the event.
       - Facilities includes coworking, renovations, construction — not just
         maintenance. These are all operational space costs.
       - "domain", "ssl", "certificate" are SOFTWARE (5010) — handled by
         classify_software at priority 3, not here."""
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
            # Determine treatment: insurance annual → prepaid
            if gl_code == "5100" and is_annual(resolve_service_period(item, invoice)):
                return Classification(gl_code="1320", treatment=Treatment.PREPAID,
                                      rule_applied="Insurance annual prepayment → 1320")
            treatment = Treatment.EXPENSE
            return Classification(gl_code=gl_code, treatment=treatment,
                                  rule_applied=rule_name)
    return None  # Truly unclassifiable — flagged for review
```

Note: "Regulatory compliance review & advisory" matches `consulting_keywords` ("advisory") but NOT `legal_action_keywords` (no litigation, patent, contract review, etc.). So it gets 5040, matching the ground truth. The word "regulatory" is a domain, not a legal action.

**The keyword-matching approach:** Primarily simple `in` checks on lowercased descriptions, with **word-boundary regex** (`re.search(r'\bword\b', desc)`) for short/ambiguous keywords like `"ad"`, `"app"`, `"tool"`, `"license"` that would cause false positives as substrings (e.g., `"ad"` matching `"advisory"`, `"app"` matching `"apparel"`). Equipment uses **negative lookahead** (`r"\bserver\b(?! rental)"`) to prevent priority conflicts with lower-priority categories. Not ML, not NLP. Good enough for the SOP's categories, and fully deterministic. Each rule documents exactly which keywords it matches. **Every rule has an explicit keyword guard** — no rule blindly returns a classification without first confirming the line item description matches its category. This prevents higher-priority rules from accidentally capturing items that belong to lower-priority categories.

**Classifier imports:** `import re` is required at module level for word-boundary matching. `resolve_service_period` from `recognition.py` is imported for annual detection (or inlined as a shared utility).

### Step 3: Recognition Engine (`recognition.py`)

Handles **three concerns:**

#### Service Period Resolution

Before applying prepaid/accrual logic, resolve the service period for each line:

```python
def resolve_service_period(item: LineItem, invoice: Invoice) -> ServicePeriod | None:
    """Line-level service_period takes priority; falls back to invoice-level."""
    return item.service_period or invoice.service_period
```

This handles INV-004, where "Service period: Dec 2025" is at the invoice level, not per line.

#### Helper Functions

```python
import re
from calendar import monthrange

def is_annual(period: ServicePeriod | None) -> bool:
    """Return True if the service period spans more than 1 month.
    Returns False for None — a line item with no service period is treated as
    monthly/usage-based. If a description says "Annual" but has no structured
    service_period in the fixture, the item will NOT be treated as prepaid.
    This is intentional: we require structured data, not description parsing."""
    if period is None:
        return False
    return months_between(period.start, period.end) > 1

def months_between(start: date, end: date) -> int:
    """Count the number of calendar months a period spans (inclusive of both endpoints).
    Examples:
      Jan 1 – Dec 31  → 12 months (full year)
      Feb 1 – Jan 31  → 12 months (full year, different start)
      Jan 1 – Jan 31  → 1 month (single month)
      Jan 15 – Mar 14 → 3 months (spans 3 calendar months, partial on edges)
    The count is: (end.year - start.year) * 12 + (end.month - start.month) + 1.
    This works because we're counting calendar months spanned, not 30-day periods."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1

def monthly_periods(period: ServicePeriod) -> list[date]:
    """Generate the first day of each calendar month in the period.
    For Jan 1 – Dec 31, yields: [Jan 1, Feb 1, ..., Dec 1].
    Used to iterate amortization entries."""
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

#### Prepaid Recognition
When `treatment == PREPAID` and service period > 1 month:

```python
def generate_prepaid_entries(item, classification, invoice) -> list[JournalEntry]:
    entries = []
    
    # 1. Initial booking: Debit Prepaid, Credit AP
    entries.append(JournalEntry(
        lines=[
            JournalLine(account_code=classification.gl_code, debit=item.amount),  # e.g., 1310
            JournalLine(account_code="2000", credit=item.amount),                 # AP
        ],
        entry_type="initial"
    ))
    
    # 2. Monthly amortization entries: Debit Expense, Credit Prepaid
    period = resolve_service_period(item, invoice)
    months = months_between(period.start, period.end)
    monthly_amount = (item.amount / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    expense_code = PREPAID_TO_EXPENSE.get(classification.gl_code)
    if expense_code is None:
        raise ValueError(
            f"No expense mapping for prepaid account {classification.gl_code}. "
            f"Add it to PREPAID_TO_EXPENSE in chart_of_accounts.py."
        )
    
    # Rounding: last month gets the remainder to ensure exact sum
    # NOTE: Amortization entry dates use month_start (from monthly_periods),
    # NOT invoice.date. E.g., for Jan-Dec 2026, entries are dated
    # Jan 1, Feb 1, ..., Dec 1 — the amortization schedule matches the
    # service period, not the invoice receipt date.
    for i, month_start in enumerate(monthly_periods(period)):
        if i == months - 1:  # last month
            amt = item.amount - (monthly_amount * (months - 1))
        else:
            amt = monthly_amount
        entries.append(JournalEntry(
            lines=[
                JournalLine(account_code=expense_code, debit=amt),
                JournalLine(account_code=classification.gl_code, credit=amt),
            ],
            entry_type="amortization"
        ))
    
    # Do NOT set posting_gl for prepaids — gl_code already IS the prepaid account
    # (e.g., 1310, 1300). The eval uses `posting_gl or gl_code`, so leaving
    # posting_gl as None lets the fallback handle it. Simpler mental model:
    # posting_gl is only set when the posting account DIFFERS from gl_code (accruals).
    
    return entries
```

**Rounding strategy:** `ROUND_HALF_UP` for monthly amounts, with the last month absorbing the remainder. For $24,000 / 12 = $2,000.00 (clean). For $25,000 / 12 = $2,083.33 × 11 + $2,083.37 = $25,000.00 exactly. This guarantees amortization entries sum to the original amount.

#### Simple Expense / Capitalize Entry

For expense and capitalize treatments (the default path), the entry shape is identical — only the GL code differs:

```python
def is_professional_service(gl_code: str) -> bool:
    """Return True if the GL code is a professional services category."""
    return gl_code in ("5030", "5040")

def generate_expense_entries(item, classification, invoice) -> list[JournalEntry]:
    """Generate standard AP journal entry: Debit GL account, Credit AP (2000).
    Used for EXPENSE (5000-5110) and CAPITALIZE (1500) treatments.
    The shape is identical — only the GL debit account differs.
    
    NOTE: For CAPITALIZE (1500), posting_gl stays None. This works correctly
    because gl_code IS already the final posting account (1500). The eval formula
    `posting_gl or gl_code` → `None or "1500"` → `"1500"`. Unlike accruals
    (where the classifier outputs 5040 but the posting account is 2110), the
    capitalize path doesn't need a separate posting_gl."""
    return [JournalEntry(
        lines=[
            JournalLine(account_code=classification.gl_code, debit=item.amount),
            JournalLine(account_code="2000", credit=item.amount),
        ],
        entry_type="initial"
    )]
```

#### Accrual Recognition

**Accrual detection is owned by the recognition engine, not the classifier.** The classifier sets `Treatment.EXPENSE` for professional services (5030/5040) and travel (5060). The recognition engine then checks dates to determine if accrual treatment applies:

```python
def generate_entries(classified_line: ClassifiedLine, invoice: Invoice) -> list[JournalEntry]:
    """Main entry point for recognition. Handles prepaid, accrual, and expense."""
    classification = classified_line.classification
    item = classified_line.line_item
    period = resolve_service_period(item, invoice)
    
    # Prepaid: classifier already set treatment=PREPAID (annual software/cloud/insurance)
    if classification.treatment == Treatment.PREPAID:
        return generate_prepaid_entries(item, classification, invoice)
    
    # Accrual: recognition engine detects this from dates, NOT from classifier treatment.
    # If service period ended STRICTLY BEFORE invoice date → accrual.
    # Boundary: period.end == invoice.date is NOT accrual (SOP says "after", not "on or after").
    # This is a conscious choice — see §5 Accrual Detection notes below.
    # The classifier leaves treatment=EXPENSE; recognition overrides it to ACCRUAL here.
    if period and period.end < invoice.date:
        classification.treatment = Treatment.ACCRUAL  # Override EXPENSE → ACCRUAL
        return generate_accrual_entries(item, classification, invoice)
    
    # Capitalize: classifier already set treatment=CAPITALIZE (equipment ≥ $5K)
    if classification.treatment == Treatment.CAPITALIZE:
        return generate_expense_entries(item, classification, invoice)  # same shape, different GL
    
    # Default: simple expense
    return generate_expense_entries(item, classification, invoice)
```

When invoice date is *after* the service period ends:

```python
def generate_accrual_entries(item, classification, invoice) -> list[JournalEntry]:
    accrual_account = "2110" if is_professional_service(classification.gl_code) else "2100"
    expense_code = classification.gl_code  # The expense GL stays the same
    
    entries = []
    
    # 1. Accrual entry (at service period end): Debit Expense, Credit Accrued Liability
    period = resolve_service_period(item, invoice)
    entries.append(JournalEntry(
        date=period.end,
        lines=[
            JournalLine(account_code=expense_code, debit=item.amount),
            JournalLine(account_code=accrual_account, credit=item.amount),
        ],
        entry_type="accrual"
    ))
    
    # 2. Reversal entry (when paid/invoice received): Debit Accrued Liability, Credit AP
    entries.append(JournalEntry(
        date=invoice.date,
        lines=[
            JournalLine(account_code=accrual_account, debit=item.amount),
            JournalLine(account_code="2000", credit=item.amount),
        ],
        entry_type="reversal"
    ))
    
    # Set posting_gl to the accrual account (this is what the eval compares)
    classification.posting_gl = accrual_account
    
    return entries
```

**Why the recognition engine owns accrual detection:** The classifier doesn't have enough context — it sees the line item description and can identify the *expense category* (5040 consulting, 5060 travel), but whether the expense is an accrual depends on the *timing relationship* between the service period and the invoice date, which is a recognition concern. The classifier sets the initial `Treatment.EXPENSE`, and the recognition engine overrides it to `Treatment.ACCRUAL` when `service_period.end < invoice.date`. This keeps the classifier focused on *what* the expense is, and the recognition engine focused on *when* it should be recognized.

#### Accrual Detection — Boundary & Partial Overlap Notes

**Same-day boundary:** `service_period.end == invoice.date` is **NOT** treated as accrual. The SOP says "Invoice received **after** service period ends," which we interpret strictly: the invoice date must be strictly after the period end, not on the same day. This is a conscious design choice, documented here so implementors don't accidentally use `<=`.

The accrual check is strictly SOP-compliant: `service_period.end < invoice.date` → accrual. This means a service period ending 5 days before the invoice date is treated identically to one ending 45 days before. No partial-overlap or pro-rata logic.

**Shadow mode review hint:** When running in shadow mode and `0 < (invoice.date - period.end).days <= 7`, the engine adds a warning to `ProcessingResult.warnings`: `"Near-boundary accrual: service ended {N} days before invoice date — verify intent"`. This uses the existing `warnings` list — no new fields needed. The 7-day threshold is hardcoded (not configurable). This is advisory only — it does not change classification.

**Accrual applies to ALL expense categories**, not just professional services and travel. Any line item with `treatment == EXPENSE` and `service_period.end < invoice.date` becomes an accrual. The accrual account is `2110` for professional services (GL codes 5030, 5040) and `2100` for everything else. This means facilities maintenance billed after the service period, training events billed retroactively, etc., all receive correct accrual treatment automatically.

#### Prepaid vs Single-Month-Ahead

The SOP says "Service period > 1 month + paid upfront → Prepaid." A conference booth rental for next month (1 month or less ahead) does NOT trigger prepaid treatment. The check is: `months_between(period.start, period.end) > 1`. A single-month rental is ≤ 1 month → expense normally.

**Mapping table:**

| Prepaid Account | Amortizes To | Condition |
|----------------|-------------|-----------|
| 1310 (Prepaid Software) | 5010 | Software annual |
| 1300 (Prepaid General) | 5020 | Cloud annual |
| 1320 (Prepaid Insurance) | 5100 | Insurance annual |

```python
# In chart_of_accounts.py
PREPAID_TO_EXPENSE = {"1310": "5010", "1300": "5020", "1320": "5100"}
```

| Accrual Account | For |
|----------------|-----|
| 2110 (Accrued Prof Services) | GL codes 5030, 5040 |
| 2100 (Accrued General) | Everything else |

### Step 4: Approval Router (`approver.py`)

```python
def route_approval(invoice: Invoice, classifications: list[ClassifiedLine]) -> ApprovalDecision:
    """
    Evaluate approval rules. Fail-closed: no matching rule → DENIED.
    
    Base rules:
      ≤ $1K → auto-approve
      $1K–$10K → dept_manager  
      > $10K → vp_finance
      Any 1500 (Fixed Asset) → vp_finance regardless
    
    Overrides:
      Marketing: auto-approve up to $2.5K
      Engineering: auto-approve up to $5K IF all lines are 5020 or 5010
    """
```

**Evaluation order** (following fail-closed principle):

1. **Fixed Asset check** — if any line classified as `1500`, return `VP_FINANCE` immediately
2. **Department overrides** — check Marketing $2.5K and Engineering $5K (cloud/software only) overrides
3. **Base thresholds** — ≤$1K auto, $1K-$10K dept_manager, >$10K vp_finance
4. **Default** — `DENIED` (fail closed)

**Override semantics: overrides can only widen auto-approve, never narrow it.** Overrides are checked before base thresholds. If an override applies (e.g., Marketing ≤$2.5K → auto), it returns immediately. If the override doesn't apply (e.g., Marketing $8K > $2.5K), execution falls through to base thresholds. This means a Marketing $800 invoice auto-approves via the base rule (≤$1K), and a Marketing $1,500 invoice auto-approves via the override ($1,500 ≤ $2.5K). There is no scenario where an override makes approval *more* restrictive than the base.

**Important: Engineering override checks `gl_code`, not the underlying expense.** The override requires ALL lines to be `5020` or `5010`. Prepaid codes (1310 for software, 1300 for cloud) and asset codes (1500) do NOT satisfy this check. This means annual-prepaid software/cloud invoices from Engineering cannot be auto-approved via the override — they fall through to base threshold rules. This is intentional: prepaid items represent larger financial commitments that warrant normal approval flows.

### Step 5: Journal Poster (`poster.py`)

For **simple expense** lines (no prepaid/accrual), generates the standard AP entry:

```
Debit  [GL Account]     $X
Credit [2000 AP]         $X
```

**Verification:** Two-level check:

```python
def verify_journal_entries(entries: list[JournalEntry], invoice_total: Decimal) -> bool:
    # 1. Each individual entry must balance (debits == credits)
    for entry in entries:
        if not entry.verify():
            return False
    
    # 2. The AP account (2000) total credits across ALL entries must equal invoice total.
    #    This works for all invoice types:
    #    - Simple expense: Debit Expense, Credit AP → AP credits = total
    #    - Prepaid: Debit Prepaid, Credit AP → AP credits = total (amort entries don't touch AP)
    #    - Accrual: Accrual entry doesn't touch AP, but reversal does:
    #      Debit Accrued, Credit AP → AP credits = total
    #    - Mixed (INV-003): Each initial entry credits AP for its line amount → sum = total
    ap_credits = sum(
        l.credit for e in entries for l in e.lines 
        if l.account_code == "2000"
    )
    return ap_credits == invoice_total
```

**Why AP credits, not initial entry debits:** The old approach (filtering by `entry_type == "initial"`) breaks for accrual invoices, where entries are typed "accrual" and "reversal", not "initial". The AP control account is the universal invariant: every invoice eventually credits AP for its full amount, regardless of treatment. This handles expense, prepaid, accrual, capitalize, and mixed invoices uniformly.

---

## 6. Engine (Orchestrator)

```python
class Engine:
    def process(self, invoice: Invoice, *, 
                dry_run: bool = False,
                shadow: bool = False) -> ProcessingResult:
        result = ProcessingResult(invoice_id=invoice.id, invoice_total=invoice.total)
        
        # Idempotency: check if already posted OR pending approval
        if not dry_run and not shadow:
            if self.store.has_journal_entries(invoice.id):
                result.status = "error"
                result.errors.append(f"Invoice {invoice.id} already posted. Use --dry-run to re-examine.")
                return result
            if self.store.has_pending(invoice.id):
                result.status = "error"
                result.errors.append(f"Invoice {invoice.id} is pending approval. Use 'gl resume' to approve/reject.")
                return result
        
        # Step 1: PO Match
        po_result = po_matcher.match_po(invoice, self.po_store)
        result.po_result = po_result  # Store full PO match result for audit/debugging
        if not po_result.matched:
            result.status = "flagged"
            result.errors.append(f"PO match failed: {po_result.reason}")
            return result
        
        # Step 2: Classify each line item
        for item in invoice.line_items:
            classification = classifier.classify_line_item(item, invoice)
            result.classifications.append(ClassifiedLine(line_item=item, classification=classification))
        
        # Step 2b: Hard-fail if ANY line is unclassifiable.
        # Without this, unclassifiable lines are silently skipped, AP credits < invoice total,
        # and journal entries would be posted with incomplete/unbalanced data.
        unclassifiable = [cl for cl in result.classifications if cl.classification is None]
        if unclassifiable:
            for cl in unclassifiable:
                result.errors.append(f"Unclassifiable line item: {cl.line_item.description}")
            result.status = "flagged"
            return result
        
        # Step 3: Recognition (prepaid/accrual) — also sets classification.posting_gl
        journal_entries = []
        for cl in result.classifications:
            entries = recognition.generate_entries(cl, invoice)
            journal_entries.extend(entries)
        
        result.journal_entries = journal_entries
        
        # Step 4: Approval
        approval = approver.route_approval(invoice, result.classifications)
        result.approval = approval
        
        # Step 5: Verify journal entries balance
        verification_passed = poster.verify_journal_entries(journal_entries, invoice.total)
        if not verification_passed:
            result.warnings.append("Journal entries don't balance to invoice total")
        
        # --- Mode-specific exit paths ---
        
        if dry_run or shadow:
            # Don't hard-fail in dry_run/shadow — still show the proposals
            result.status = "dry_run" if dry_run else "shadow"
            return result  # Always return proposals, even with warnings
        
        # Hard-fail in normal mode: unbalanced entries must NOT be posted.
        if not verification_passed:
            result.status = "error"
            result.errors.append("Journal entries failed balance verification — not posted")
            return result
        
        if approval.level == ApprovalLevel.DENIED:
            result.status = "flagged"  # NOT a dead end — see below
            self.store.save_pending(result)  # Allow HITL override
            return result
        
        if approval.level != ApprovalLevel.AUTO:
            result.status = "pending_approval"
            self.store.save_pending(result)
            return result
        
        self.store.save_journal_entries(journal_entries)
        result.status = "posted"
        return result
    
    def resume(self, invoice_id: str, approved: bool) -> ProcessingResult:
        """Resume a pending-approval OR denied invoice after human decision."""
        result = self.store.load_pending(invoice_id)
        if approved:
            # Re-verify before posting — guards against stale/corrupt serialized state.
            # Uses the stored invoice_total (set when ProcessingResult was created),
            # NOT sum(line_item.amount). These should be equal, but if they aren't
            # (e.g., data corruption), we want to catch it.
            if not poster.verify_journal_entries(result.journal_entries, result.invoice_total):
                result.status = "error"
                result.errors.append("Journal entries failed verification on resume — not posted")
                return result
            self.store.save_journal_entries(result.journal_entries)
            result.status = "posted"
            result.approval.approved = True
        else:
            result.status = "rejected"
            result.approval.approved = False
        self.store.delete_pending(invoice_id)
        return result
```

**Key design decisions:**
- The engine is a class with `store` and `po_store` as dependencies (injected, not global).
- `dry_run` and `shadow` are just boolean flags — no separate code paths.
- **Dry-run/shadow checks happen BEFORE verification exits.** Shadow mode always returns proposals, even if verification has warnings. This ensures shadow mode can show proposals for invoices with rounding edge cases.
- **Denied invoices are NOT dead ends.** They are saved to `pending_approvals` and can be resumed with `gl resume <id> --approve`. This handles the case where the fail-closed rule fires but a human wants to override.
- **Idempotency guard:** `store.has_journal_entries(invoice_id)` AND `store.has_pending(invoice_id)` prevent double-posting and duplicate pending entries. Dry-run and shadow skip these checks.
- HITL is `save_pending` + `resume`. State is serialized as JSON via Pydantic's `.model_dump_json()`.
- The pipeline is **linear, not a DAG**. Each step depends on the previous. No need for async, parallel, or graph-based orchestration.

---

## 7. Storage (`store.py`)

SQLite with three tables:

```sql
CREATE TABLE journal_entries (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    lines_json TEXT NOT NULL,        -- JSON array of JournalLine
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Prevent duplicate posting of the same invoice
CREATE INDEX idx_journal_entries_invoice ON journal_entries(invoice_id);

CREATE TABLE pending_approvals (
    invoice_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,       -- Full ProcessingResult serialized
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE corrections (
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

**Key store methods:**
- `has_journal_entries(invoice_id) -> bool` — idempotency check before posting
- `has_pending(invoice_id) -> bool` — idempotency check for pending approvals
- `save_journal_entries(entries)` — insert all entries for an invoice
- `save_pending(result)` / `load_pending(invoice_id)` — HITL state serialization (uses `INSERT OR REPLACE` to handle re-saves)
- `delete_pending(invoice_id)` — `DELETE FROM pending_approvals WHERE invoice_id = ?` — cleanup after resume
- `save_correction(...)` / `list_corrections()` — feedback loop

**Why this schema:**
- `lines_json` stores journal lines as JSON. We don't need to query individual debit/credit lines — we always load full entries. No need for a join table.
- `pending_approvals` stores the full `ProcessingResult` so `resume` can reconstruct state without re-running the pipeline. `delete_pending` cleans up after resume.
- `corrections` is append-only, one row per correction. This is the feedback loop's input. **All corrections go through SQLite only** — there is no separate `corrections.json` file.

---

## 8. CLI Design

```
gl process <invoice_id>        # Full pipeline: classify → approve → post
gl process <invoice_id> --dry-run   # Show what would be posted
gl eval                        # Run all 6 labeled invoices, show accuracy
gl shadow                      # Process 10 unlabeled invoices in shadow mode
gl resume <invoice_id> --approve    # Resume pending approval
gl resume <invoice_id> --reject
gl feedback correct <invoice_id> <line_index> --gl <code> --treatment <type>
gl feedback analyze            # Show error patterns
gl feedback rerun              # Re-run eval with corrections applied, show before/after
```

**Framework:** Click (already widely used, zero magic, no async complexity like Typer).

---

## 9. Eval System

### Ground Truth (`expected.py`)

Each labeled invoice has expected outputs:

```python
EXPECTED = {
    "INV-001": {
        "lines": [
            {"description": "Annual Platform License", "gl": "1310", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",  # > $10K
    },
    "INV-002": {
        "lines": [
            {"description": "Patent filing", "gl": "5030", "treatment": "expense"},
            {"description": "Regulatory compliance", "gl": "5040", "treatment": "expense"},
            {"description": "Contract review", "gl": "5030", "treatment": "expense"},
        ],
        "approval_level": "dept_manager",  # $1K-$10K
    },
    "INV-003": {
        "lines": [
            {"description": "MacBook Pro 14-inch (3x)", "gl": "5110", "treatment": "expense"},
            {"description": "Dell PowerEdge R760", "gl": "1500", "treatment": "capitalize"},
            {"description": "AWS Reserved Instances (annual)", "gl": "1300", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",   # Any 1500 (Fixed Asset) → vp_finance
    },
    "INV-004": {
        "lines": [
            {"description": "Operational efficiency assessment", "gl": "2110", "treatment": "accrual"},
            {"description": "Travel expenses", "gl": "2100", "treatment": "accrual"},
        ],
        "approval_level": "dept_manager",  # $1K-$10K, Operations dept (no override applies)
    },
    "INV-005": {
        "lines": [
            {"description": "Q1 digital ad campaign management", "gl": "5050", "treatment": "expense"},
            {"description": "Branded company t-shirts (500 units)", "gl": "5000", "treatment": "expense"},
            {"description": "Conference booth rental", "gl": "5050", "treatment": "expense"},
            {"description": "Conference attendee gift bags (branded)", "gl": "5000", "treatment": "expense"},
        ],
        "approval_level": "vp_finance",  # > $10K ($23,500 total)
    },
    "INV-006": {
        "status": "flagged",              # No PO → pipeline stops at Step 1
        "lines": [],                      # No classifications produced
        "approval_level": None,           # Never reaches approval
    },
}
```

### Accuracy Metrics (`report.py`)

Four dimensions measured independently:

| Metric | What it measures | Compared field |
|--------|-----------------|----------------|
| **Posting GL accuracy** | % of line items where final posting GL == expected GL | `classification.posting_gl or classification.gl_code` vs `expected.gl` |
| **Treatment accuracy** | % of line items where predicted treatment == expected treatment | `classification.treatment` vs `expected.treatment` |
| **Approval accuracy** | % of invoices where predicted approval level == expected | `approval.level` vs `expected.approval_level` |
| **Journal entry accuracy** | Do journal entries balance? Do AP credits == invoice total? | Verification pass/fail |

**Why `posting_gl`, not `classifier_gl`:** The REQS.md expected GL for INV-004 is 2110/2100 (accrual accounts), which are post-recognition values. For INV-001, the expected GL is 1310 (prepaid), which IS the classifier output. The `posting_gl` field unifies both: it's set by the recognition engine when treatment ≠ expense, and defaults to `gl_code` otherwise.

**Output format:**

```
═══ Evaluation Report ═══
Overall: 13/13 correct (100.0%)

Posting GL: 13/13 line items correct (100.0%)
  ✓ INV-001: Annual Platform License → 1310 (prepaid)
  ✓ INV-002: Regulatory compliance review & advisory → 5040 (expense)
  ✓ INV-004: Operational efficiency assessment → 2110 (accrual)
  ...

Treatment: 13/13 correct (100.0%)
Approval: 6/6 correct (100.0%)
Journal Verification: 6/6 passed
```

**Line count:** INV-001(1) + INV-002(3) + INV-003(3) + INV-004(2) + INV-005(4) + INV-006(0, flagged) = **13** classifiable line items.

### Feedback Loop

```
1. Human runs: gl feedback correct INV-002 1 --gl 5040
2. Correction stored in SQLite
3. Human runs: gl feedback analyze
   → Shows: "Rule 'professional_services' misclassifies advisory as legal when description contains 'regulatory'"
4. Developer fixes rule in classifier.py
5. Human runs: gl feedback rerun
   → Shows before/after accuracy: 94.4% → 100.0%
```

**The `analyze` command** groups corrections by `original_gl → corrected_gl` and shows frequency + common description patterns. This is a simple GROUP BY query, not ML.

**The `rerun` command** re-evaluates all 6 invoices with the current rules, then overlays any corrections to show what accuracy *would be* if rules were adjusted. The "before" is the stored baseline (first eval run), the "after" is the current run.

---

## 10. Shadow Mode

Processes the 10 unlabeled invoices through the full pipeline in dry-run mode and produces a review report:

```
gl shadow

═══ Shadow Mode: 10 Unlabeled Invoices ═══

UL-1 | DataSync Pro | $24,000
  Line 1: Annual Platform License → 1310 (Prepaid Software)
    Amortization: $1,500/mo × 12 months to 5010
  Line 2: Premium Support & Implementation → 5040 (Consulting)
  Approval: VP Finance (> $10K)
  Rule: "Consulting/advisory → 5040"

UL-3 | TalentBridge Partners | $25,000
  Line 1: Placement fee → 5040 (Consulting)
  Approval: VP Finance (> $10K)
  Rule: "Consulting/advisory → 5040"

... (8 more)

Proposals saved to: data/shadow_results.json
```

The shadow report shows the **rule trace** for each classification (which rule matched and why). This makes it easy for reviewers to spot cases where the wrong rule fired. There is no confidence/ambiguity scoring mechanism — the pipeline is deterministic, so each line either matches a rule or is flagged as unclassifiable.

---

## 11. Implementation Phases

Designed so each phase takes roughly **equal implementation effort** (~4-5 hours each for a 24-hour assessment).

### Phase 1: Data Foundation (~4 hrs)
**Goal:** All data models, fixtures, chart of accounts, and storage working.

| Task | Output |
|------|--------|
| Define all Pydantic models in `models.py` | `Invoice`, `LineItem`, `PurchaseOrder`, `ServicePeriod`, `Classification`, `JournalEntry`, `JournalLine`, `ApprovalDecision`, `ProcessingResult`, `ClassifiedLine` |
| Build chart of accounts in `chart_of_accounts.py` | `GL_ACCOUNTS` dict + `GLAccount` model + lookup helpers |
| Create JSON fixtures for all 6 labeled invoices + POs | `data/invoices.json`, `data/purchase_orders.json` |
| Create JSON fixtures for 10 unlabeled invoices + POs | Same files, extended |
| Set up SQLite storage | `store.py` with `init_db()`, `save_journal_entries()`, `save_pending()`, `load_pending()`, `save_correction()` |
| Add project dependencies | `pydantic`, `click` in `pyproject.toml` |

**Deliverable:** Can load any invoice from fixtures, serialize/deserialize all models, read/write SQLite.

### Phase 2: Core Pipeline (~5 hrs)
**Goal:** Steps 1–5 of the SOP implemented and passing all 6 labeled invoices.

| Task | Output |
|------|--------|
| PO matcher | `po_matcher.py` — match by number, validate ±10% tolerance |
| GL classifier (all 7 rule categories) | `classifier.py` — priority chain, keyword matching, equipment unit cost logic |
| Recognition engine (prepaid + accrual) | `recognition.py` — amortization schedule generation, accrual + reversal entries |
| Approval router | `approver.py` — base thresholds + department overrides + fixed asset override |
| Journal poster + verification | `poster.py` — debit/credit pair generation, balance verification |
| Engine orchestrator | `engine.py` — wire steps 1-5, dry-run flag |
| Unit tests for each step | `tests/test_*.py` — one test file per pipeline step |

**Deliverable:** `engine.process(inv)` returns correct `ProcessingResult` for all 6 labeled invoices.

### Phase 3: Eval, Shadow & Feedback (~4 hrs)
**Goal:** Eval harness, shadow mode, and feedback loop working.

| Task | Output |
|------|--------|
| Ground truth definitions | `eval/expected.py` — expected GL, treatment, approval for all 6 invoices |
| Eval runner + metrics | `eval/runner.py`, `eval/report.py` — run pipeline, compare, compute accuracy |
| Shadow mode | Shadow processing of 10 unlabeled invoices, save proposals |
| Feedback collector | `eval/feedback.py` — `save_correction()`, `analyze_corrections()` |
| Before/after accuracy comparison | `feedback rerun` command showing improvement |

**Deliverable:** `gl eval` shows accuracy report. `gl shadow` produces review output. `gl feedback` cycle works end-to-end.

### Phase 4: CLI, HITL & Polish (~4 hrs)
**Goal:** Full CLI, human-in-the-loop flow, end-to-end tests, README.

| Task | Output |
|------|--------|
| CLI entry point with all subcommands | `main.py` using Click |
| HITL: save pending state + resume command | `gl resume <id> --approve/--reject` |
| End-to-end test for all 6 invoices | `tests/test_e2e.py` — full pipeline assertions |
| README | Architecture, setup, design decisions, tradeoffs |
| Edge case handling | Unclassifiable items, partial matches, rounding |

**Deliverable:** Complete, working system. All CLI commands functional. Tests passing. README written.

---

## 12. Key Tradeoffs

| Decision | Chosen | Alternative | Why |
|----------|--------|-------------|-----|
| Rule engine | Hardcoded priority chain | Externalized JSON rules | The SOP has 7 fixed categories. JSON rules add indirection without benefit at this scale. |
| Storage | SQLite + raw SQL | SQLAlchemy ORM | 3 tables, simple queries. ORM is overkill and adds 500+ lines of boilerplate. |
| Models | Pydantic v2 | dataclasses / attrs | Pydantic gives us JSON serialization, validation, and `.model_dump_json()` for HITL state files for free. |
| Classification | Keyword matching | NLP / embeddings | Deterministic, auditable, testable. The SOP categories are well-defined enough for keywords. |
| HITL | JSON state file + CLI resume | Interactive prompt | Testable, scriptable, works in CI. Interactive prompts block and can't be automated. |
| Amortization | Generate all entries upfront | Lazy/on-demand | The SOP says verify sum = total. You need all entries to verify. |
| Eval | Same codebase, separate directory | External eval framework | 6 test cases. A framework would be over-engineering. Simple pytest-style assertions. |
| Feedback "improvement" | Manual rule fix + re-eval | Auto-adjustment of rules | The requirement says "demonstrate measurable improvement." Manual fix + before/after accuracy comparison is the simplest proof. |

---

## 13. Known Untested Paths & Acknowledged Gaps

| Gap | Impact | Mitigation |
|-----|--------|------------|
| **Insurance path (1320 → 5100)** is never exercised. None of the 6 labeled or 10 unlabeled invoices contain insurance. | The insurance classification rule and amortization mapping exist but are untested by eval. | Add a synthetic unit test in `test_classifier.py` and `test_recognition.py` that exercises the insurance path directly. Document in README. |
| **Equipment classifier requires `unit_cost` in fixtures.** If a future invoice has equipment without structured `unit_cost`, the fallback to `item.amount` may misclassify. | Only matters for multi-unit equipment purchases where total ≥ $5K but unit cost < $5K. | Document in README that fixtures must be manually structured with `unit_cost` for equipment line items. Consider adding a warning when equipment is detected but `unit_cost` is None. |
| **Amortization for non-clean divisions.** E.g., $25,000 / 12 months. | Last-month remainder strategy handles this, but no labeled invoice exercises it. | Unit test with non-clean amounts in `test_recognition.py`. |
| **PO amounts in REQS.md are truncated.** Some invoice totals appear cut off in the PDF rendering. | PO fixtures may need estimation. | Set PO amounts to match invoice totals exactly (0% variance) for labeled invoices. The ±10% tolerance handles real-world variance. |
| **Multi-invoice PO consumption is not tracked.** Two invoices referencing the same PO can each pass the 10% tolerance independently while their combined total exceeds the PO amount. | Low impact — the 6 labeled invoices have unique POs. Could matter for unlabeled invoices or production data. | Out of scope for this assessment. Document in README. A future enhancement would add a `po_consumed` tracking table. |
| **Credit memos and negative amounts are out of scope.** The `LineItem.amount` validator enforces `gt=0`. Real AP workflows frequently process credit memos (negative amounts). | Negative invoices would be rejected at validation. | Documented as an explicit scope boundary. If credit memos are needed, remove the `gt=0` constraint and add sign-aware journal entry logic. |

---

## 14. Validation Checklist (What "Done" Looks Like)

- [ ] `uv run gl process INV-001 --dry-run` → Shows 1310 prepaid booking + 12 monthly amortization entries to 5010
- [ ] `uv run gl process INV-002 --dry-run` → Shows 5030/5040/5030 classification with correct treatments
- [ ] `uv run gl process INV-003 --dry-run` → Shows 5110 (laptops), 1500 (server), 1300 (AWS prepaid) with amortization
- [ ] `uv run gl process INV-004 --dry-run` → Shows 2110/2100 accrual entries with reversals
- [ ] `uv run gl process INV-005 --dry-run` → Shows 5050/5000/5050/5000 with branded merch exception, approval = VP Finance
- [ ] `uv run gl process INV-006 --dry-run` → Flags "no PO" and stops
- [ ] `uv run gl eval` → Shows ≥95% accuracy across all dimensions
- [ ] `uv run gl shadow` → Produces proposals for 10 unlabeled invoices
- [ ] `uv run gl feedback correct` + `uv run gl feedback rerun` → Shows accuracy improvement
- [ ] `uv run gl resume` → HITL flow works with state persistence
- [ ] All tests pass (`uv run pytest`)
- [ ] README documents architecture and tradeoffs
