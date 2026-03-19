# gl — Accounts Payable Agent: Agentic System Design

> **Core Principle:** The LLM is the brain. It reads the SOP, reasons about each invoice, and calls deterministic Python tools for math, lookups, and state mutations. The human stays in the loop for approval decisions and accuracy corrections.

---

## 1. Why an Agent Architecture

The REQS.md opens with: *"Build an AI agent that automates an Accounts Payable workflow."* The previous design implemented a deterministic rule engine — robust, but not an agent. This revised design makes the **LLM the orchestrator** of the entire AP workflow. It reads the SOP from its system prompt, reasons about each invoice line-by-line, and calls Python tools to perform exact math, lookups, and state changes.

### What Changes vs. the Previous Design

| Component | Previous (Rule Engine) | New (Agent) |
|-----------|----------------------|-------------|
| **Orchestrator** | `engine.py` — Python function calling steps 1→5 sequentially | LLM Agent — reads SOP, decides which tool to call next |
| **Classifier** | `classifier.py` — 150 lines of regex/keyword rules | LLM reasoning — reads line item description, applies SOP rules from its prompt |
| **Recognition** | `recognition.py` — Python if/else for prepaid/accrual | `calculate_amortization` and `check_accrual` tools — LLM decides WHEN to call them based on SOP Step 3 |
| **Approval** | `approver.py` — Python threshold checks | `check_approval_threshold` tool — LLM calls it, reads the result, pauses if needed |
| **Poster** | `poster.py` — Python journal entry builder | `build_journal_entries` tool — LLM calls it with classifications, gets balanced entries back |
| **HITL** | `store.save_pending()` + CLI `resume` | Agent emits structured `pending_approval` status. CLI serializes state. `resume` re-invokes agent with approval decision injected. |
| **Feedback** | Human edits regex rules manually | Human corrections become few-shot examples in the agent's prompt. Agent self-improves. |

### What Does NOT Change

- **Data models** (`models.py`) — Invoice, LineItem, JournalEntry, Classification, etc. remain identical.
- **Chart of Accounts** (`chart_of_accounts.py`) — Static lookup, unchanged.
- **Fixtures** (`fixtures.py`) — JSON loading, unchanged.
- **Store** (`store.py`) — SQLite persistence, unchanged.
- **CLI** (`main.py`) — Click commands, unchanged (only the wiring inside each command changes).

---

## 2. Framework Choice: PydanticAI

### Why PydanticAI over LangGraph

| Criterion | PydanticAI | LangGraph |
|-----------|-----------|-----------|
| **Complexity** | FastAPI-like decorators, minimal boilerplate | Graph DSL, nodes, edges, state reducers — heavy for a linear workflow |
| **Structured Output** | Native `output_type=MyPydanticModel` — LLM output is validated by Pydantic automatically | Manual JSON parsing or chained tool calls |
| **Dependency Injection** | `deps_type` + `RunContext` — clean, testable, injectable | Manual state passing through TypedDict |
| **Tool Definition** | `@agent.tool` decorator — docstrings become tool descriptions | `@tool` decorator + manual binding to graph nodes |
| **HITL** | Agent returns structured output with `pending_approval` status; CLI handles pause/resume | Built-in `interrupt()` — powerful, but requires LangGraph server runtime |
| **Testing** | Override model with `TestModel` for deterministic tests | Requires mocking the entire graph execution |
| **Dependencies** | `pydantic-ai` (+ `openai`/`anthropic` as model backend) | `langgraph` + `langchain-core` + `langchain-openai` — heavy dependency tree |

**Decision:** PydanticAI. It's simpler, testable, Pythonic, and doesn't require a graph runtime. Our workflow is fundamentally linear (process one invoice at a time), not a DAG. LangGraph's power is in complex multi-agent graphs — overkill here.

### Dependencies

```toml
[project]
name = "varick"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "click>=8.1",
    "pydantic>=2.0",
    "pydantic-ai>=0.0.49",
    "python-dotenv>=1.0",
]

[project.scripts]
gl = "main:cli"
```

The LLM provider (OpenAI, Anthropic, Google) is configured via environment variable. PydanticAI is model-agnostic — swap providers without code changes.

```bash
# .env
OPENAI_API_KEY=sk-...
```

---

## 3. Architecture

### High-Level Flow

```
Invoice JSON
     │
     ▼
┌─────────────────────────────────────────────────┐
│              AP Agent (LLM Brain)               │
│                                                 │
│  System Prompt: Full SOP + Chart of Accounts    │
│  + Few-Shot Corrections (from feedback DB)      │
│                                                 │
│  The agent reads the invoice, reasons about     │
│  each line item, and calls tools as needed:     │
│                                                 │
│  ┌──────────────┐  ┌──────────────────────┐     │
│  │ match_po()   │  │ classify_line_item() │     │
│  └──────┬───────┘  └──────────┬───────────┘     │
│         │                     │                 │
│  ┌──────┴───────┐  ┌──────────┴───────────┐     │
│  │ PO Store     │  │ Chart of Accounts    │     │
│  │ (JSON)       │  │ (Static Lookup)      │     │
│  └──────────────┘  └─────────────────────-┘     │
│                                                 │
│  ┌─────────────────────┐  ┌────────────────┐    │
│  │ calculate_amortize()│  │ check_accrual()│    │
│  └──────────┬──────────┘  └───────┬────────┘    │
│             │                     │             │
│  ┌──────────┴─────────────────────┴────────┐    │
│  │         Deterministic Python Math       │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  ┌──────────────────────┐                       │
│  │ check_approval()     │──→ Returns threshold  │
│  └──────────────────────┘    decision            │
│                                                 │
│  ┌──────────────────────┐                       │
│  │ build_and_verify_    │──→ Returns balanced   │
│  │ journal_entries()    │    JE or error         │
│  └──────────────────────┘                       │
│                                                 │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
            Structured Output:
            ProcessingResult (Pydantic Model)
```

### Module Map

```
varick/
├── main.py                          # CLI entry point (Click)
├── pyproject.toml
├── .env                             # API keys (gitignored)
├── src/
│   ├── __init__.py
│   ├── models.py                    # ALL data models (unchanged)
│   ├── chart_of_accounts.py         # GL account registry (unchanged)
│   ├── agent.py                     # ★ NEW: PydanticAI agent definition + tools
│   ├── tools.py                     # ★ NEW: Deterministic tool functions
│   ├── prompts.py                   # ★ NEW: System prompt builder (SOP + few-shot)
│   ├── store.py                     # SQLite persistence (unchanged)
│   └── fixtures.py                  # Load invoices + POs from JSON (unchanged)
├── data/
│   ├── invoices.json                # All 16 invoices
│   └── purchase_orders.json         # PO fixtures
├── eval/
│   ├── expected.py                  # Ground truth for 6 labeled invoices
│   ├── harness.py                   # Eval runner + accuracy metrics
│   └── feedback.py                  # Correction collection + analysis
├── tests/
│   ├── test_tools.py                # Unit tests for deterministic tools
│   ├── test_agent.py                # Agent tests with PydanticAI TestModel
│   └── test_e2e.py                  # End-to-end tests
└── specs/
```

**Key difference:** There is no longer a `src/pipeline/` directory. The 5 pipeline steps (po_matcher, classifier, recognition, approver, poster) are **collapsed into tool functions** that the agent calls. The orchestration logic that was in `engine.py` is now in the LLM's reasoning.

---

## 4. The Agent (`src/agent.py`)

### Agent Definition

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

from src.models import Invoice, ProcessingResult
from src.store import Store
from src.fixtures import load_purchase_orders
from src.prompts import build_system_prompt


@dataclass
class AgentDeps:
    """Dependencies injected into every tool call."""
    invoice: Invoice
    po_store: dict           # PO number → PurchaseOrder
    store: Store             # SQLite persistence
    dry_run: bool = False
    shadow: bool = False


ap_agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=AgentDeps,
    output_type=ProcessingResult,
    instructions=build_system_prompt,  # Dynamic — includes few-shot corrections
    retries=2,
)
```

### Why `output_type=ProcessingResult`

PydanticAI validates the LLM's final output against this Pydantic model. If the LLM produces an invalid GL code, a malformed journal entry, or a missing field, **PydanticAI automatically retries** with the validation error injected into the conversation. This gives us structured, type-safe output without manual JSON parsing.

### Instructions as a Callable

```python
# src/prompts.py

def build_system_prompt(ctx: RunContext[AgentDeps]) -> str:
    """
    Dynamically builds the system prompt. This is called ONCE per agent invocation.
    Includes:
    1. The full SOP (Steps 1-5)
    2. The Chart of Accounts
    3. Few-shot corrections from the feedback database
    """
    corrections = ctx.deps.store.list_corrections()
    
    base_prompt = """You are an Accounts Payable agent. Your job is to process a vendor invoice 
through the company's Standard Operating Procedure (SOP). You MUST follow the SOP exactly, 
in order, using the tools provided.

## Your Workflow

1. **Step 1 — PO Matching:** Call `match_po` to verify the invoice has a valid purchase order.
   - If no PO or PO doesn't match → set status to "flagged" and STOP. Do not classify.
   
2. **Step 2 — Line-Item Classification:** For EACH line item in the invoice, determine the correct 
   GL account code using the Classification Rules below. You MUST classify every line before 
   moving to Step 3.

3. **Step 3 — Recognition:** For each classified line, determine if it needs prepaid or accrual 
   treatment:
   - **Prepaid:** If service period > 1 month AND paid upfront → call `calculate_amortization` 
     to get the monthly schedule.
   - **Accrual:** If the invoice date is AFTER the service period ended → call `check_accrual` 
     to get the accrual entries.
   - **Expense:** If neither → it's a simple expense. No tool call needed.

4. **Step 4 — Approval:** Call `check_approval` with your classifications. Follow its decision:
   - auto_approve → proceed to Step 5
   - dept_manager or vp_finance → set status to "pending_approval" and STOP
   - denied → set status to "denied" and STOP

5. **Step 5 — Post:** Call `build_journal_entries` with all classifications and treatments. 
   Verify the result is balanced. If dry_run or shadow mode, do NOT call `save_to_db`.

## Classification Rules (Priority Order — FIRST match wins)

1. **Physical goods:** Office supplies, stationery, toner → 5000. 
   Branded merchandise (t-shirts, swag, gift bags) → 5000 even if from Marketing department.
2. **Equipment:** Unit cost < $5,000 → 5110. Unit cost ≥ $5,000 → 1500 (capitalize).
3. **Software/SaaS:** Monthly/usage-based → 5010. Annual prepayment → 1310 (prepaid, amortize to 5010).
4. **Cloud hosting:** Monthly → 5020. Annual prepayment → 1300 (prepaid, amortize to 5020).
5. **Professional services:** 
   - Legal (litigation, patent filing, contract drafting, regulatory enforcement) → 5030
   - Consulting/advisory (assessment, analysis, advisory, review) → 5040
   - Mixed legal + non-legal → 5030 (conservative)
6. **Marketing:** Ad spend, campaigns, sponsorships, booth rental → 5050. 
   EXCEPTION: Branded physical merchandise → 5000 (rule 1 takes priority).
7. **Other:** Travel/meals → 5060. Facilities/maintenance → 5070. Training/conferences → 5080. 
   Telecom/internet → 5090. Insurance annual → 1320 (prepaid, amortize to 5100).

## Chart of Accounts

| Code | Account | Category |
|------|---------|----------|
| 1300 | Prepaid Expenses (General) | Prepaid |
| 1310 | Prepaid Software | Prepaid |
| 1320 | Prepaid Insurance | Prepaid |
| 1500 | Fixed Assets | Asset |
| 2100 | Accrued Expenses (General) | Accrual |
| 2110 | Accrued Professional Services | Accrual |
| 5000 | Office Supplies | Expense |
| 5010 | Software & Subscriptions | Expense |
| 5020 | Cloud Hosting & Infrastructure | Expense |
| 5030 | Professional Services — Legal | Expense |
| 5040 | Professional Services — Consulting | Expense |
| 5050 | Marketing & Advertising | Expense |
| 5060 | Travel & Entertainment | Expense |
| 5070 | Facilities & Maintenance | Expense |
| 5080 | Training & Development | Expense |
| 5090 | Telecom & Internet | Expense |
| 5100 | Insurance Expense | Expense |
| 5110 | Equipment (under $5,000) | Expense |

## CRITICAL RULES

- Use Decimal arithmetic for ALL money calculations. Never use float.
- When classifying, apply the rules in PRIORITY ORDER. Stop at the FIRST match.
- For equipment, use the UNIT cost (not total cost) to determine the $5K threshold.
- "Regulatory compliance review & advisory" is CONSULTING (5040), not legal. The service 
  type is "advisory", "regulatory" is the domain, not the legal action.
"""

    # Inject few-shot corrections from feedback database
    if corrections:
        correction_block = "\n## Past Corrections (Learn From These)\n\n"
        correction_block += "The following are corrections made by human reviewers. "
        correction_block += "You MUST apply these lessons:\n\n"
        for c in corrections:
            correction_block += (
                f"- Invoice {c.invoice_id}, Line {c.line_index}: "
                f"Was classified as {c.original_gl}, should be {c.corrected_gl}. "
                f"Reason: {c.reason}\n"
            )
        base_prompt += correction_block

    return base_prompt
```

---

## 5. Tools (`src/tools.py`)

These are **deterministic Python functions** that the LLM calls. They perform exact math, lookups, and state changes. The LLM is never asked to do arithmetic.

### Tool 1: `match_po`

```python
@ap_agent.tool
def match_po(ctx: RunContext[AgentDeps]) -> str:
    """
    Match the invoice to its purchase order. Call this FIRST for every invoice.
    Returns the match result: 'matched', 'no_po', 'unmatched', or 'variance_exceeded'.
    """
    invoice = ctx.deps.invoice
    if not invoice.po_number:
        return "no_po: This invoice has no PO number. Flag for manual review. Do NOT classify."
    
    po = ctx.deps.po_store.get(invoice.po_number)
    if not po:
        return f"unmatched: PO {invoice.po_number} not found in the system. Flag for review."
    
    variance = abs(invoice.total - po.amount) / po.amount
    if variance > Decimal("0.10"):
        return (
            f"variance_exceeded: Invoice total ${invoice.total} vs PO amount ${po.amount} "
            f"(variance: {variance:.1%}, exceeds 10% tolerance). Flag for review."
        )
    
    return f"matched: PO {invoice.po_number} matches. Amount variance: {variance:.1%}. Proceed to classification."
```

### Tool 2: `get_invoice_details`

```python
@ap_agent.tool
def get_invoice_details(ctx: RunContext[AgentDeps]) -> str:
    """
    Get the full details of the invoice being processed, including all line items,
    amounts, dates, department, and service periods. Call this to inspect the invoice.
    """
    inv = ctx.deps.invoice
    lines = []
    for i, item in enumerate(inv.line_items):
        line = f"  Line {i}: {item.description} | Amount: ${item.amount}"
        if item.unit_cost:
            line += f" | Unit cost: ${item.unit_cost}"
        if item.quantity:
            line += f" | Qty: {item.quantity}"
        if item.service_period:
            line += f" | Service period: {item.service_period.start} to {item.service_period.end}"
        lines.append(line)
    
    return (
        f"Invoice: {inv.id}\n"
        f"Vendor: {inv.vendor}\n"
        f"PO: {inv.po_number or 'NONE'}\n"
        f"Date: {inv.date}\n"
        f"Department: {inv.department}\n"
        f"Total: ${inv.total}\n"
        f"Line Items:\n" + "\n".join(lines)
    )
```

### Tool 3: `calculate_amortization`

```python
@ap_agent.tool_plain
def calculate_amortization(
    total_amount: str,
    prepaid_account: str,
    expense_account: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Calculate a monthly amortization schedule for a prepaid item.
    Call this when an item has a service period > 1 month and is paid upfront.
    
    Args:
        total_amount: The total prepaid amount (e.g., "24000.00")
        prepaid_account: The prepaid GL account code (e.g., "1310")
        expense_account: The expense GL account to amortize into (e.g., "5010")
        start_date: Service period start (YYYY-MM-DD)
        end_date: Service period end (YYYY-MM-DD)
    
    Returns: JSON with the initial booking entry and monthly amortization entries.
    """
    amount = Decimal(total_amount)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    
    # Calculate months
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if months < 1:
        return json.dumps({"error": "Service period is less than 1 month. Not prepaid."})
    
    monthly = (amount / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    remainder = amount - (monthly * months)
    
    schedule = {
        "treatment": "prepaid",
        "prepaid_account": prepaid_account,
        "expense_account": expense_account,
        "initial_booking": {
            "debit": prepaid_account,
            "credit": "2000",
            "amount": str(amount),
        },
        "monthly_entries": [],
    }
    
    current = start
    for i in range(months):
        entry_amount = monthly + remainder if i == 0 else monthly
        schedule["monthly_entries"].append({
            "month": current.strftime("%Y-%m"),
            "debit": expense_account,
            "credit": prepaid_account,
            "amount": str(entry_amount),
        })
        # Advance month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    return json.dumps(schedule)
```

### Tool 4: `check_accrual`

```python
@ap_agent.tool_plain
def check_accrual(
    invoice_date: str,
    service_period_end: str,
    line_amount: str,
    is_professional_services: bool,
) -> str:
    """
    Check if a line item requires accrual treatment.
    Call this when the service was delivered BEFORE the invoice date.
    
    Args:
        invoice_date: The invoice date (YYYY-MM-DD)
        service_period_end: When the service period ended (YYYY-MM-DD)
        line_amount: The line item amount (e.g., "7500.00")
        is_professional_services: True if this is a professional services line
    
    Returns: JSON with accrual entries (accrual + reversal) or "not_accrual" if not applicable.
    """
    inv_date = date.fromisoformat(invoice_date)
    svc_end = date.fromisoformat(service_period_end)
    
    if svc_end >= inv_date:
        return json.dumps({"treatment": "not_accrual", "reason": "Service period has not ended before invoice date."})
    
    amount = Decimal(line_amount)
    accrual_account = "2110" if is_professional_services else "2100"
    
    return json.dumps({
        "treatment": "accrual",
        "accrual_account": accrual_account,
        "entries": [
            {
                "type": "accrual",
                "debit": accrual_account,
                "credit": "2000",
                "amount": str(amount),
                "period": svc_end.strftime("%Y-%m"),
            },
            {
                "type": "reversal",
                "debit": "2000",
                "credit": accrual_account,
                "amount": str(amount),
                "period": inv_date.strftime("%Y-%m"),
            },
        ],
    })
```

### Tool 5: `check_approval`

```python
@ap_agent.tool
def check_approval(
    ctx: RunContext[AgentDeps],
    classifications_json: str,
) -> str:
    """
    Determine the approval level required for this invoice based on the SOP.
    Call this AFTER classifying all line items.
    
    Args:
        classifications_json: JSON array of {"gl_code": "5030", "treatment": "expense"} 
                              for each line item.
    
    Returns: The approval decision: "auto_approve", "dept_manager", "vp_finance", or "denied".
             Includes the reason.
    """
    invoice = ctx.deps.invoice
    classifications = json.loads(classifications_json)
    gl_codes = [c["gl_code"] for c in classifications]
    
    # Rule: Any Fixed Asset (1500) → VP Finance regardless
    if "1500" in gl_codes:
        return "vp_finance: Fixed Asset (1500) detected. Requires VP Finance approval regardless of amount."
    
    total = invoice.total
    dept = invoice.department
    
    # Override: Marketing auto-approve up to $2.5K
    if dept == "Marketing" and total <= Decimal("2500"):
        return "auto_approve: Marketing department auto-approve up to $2,500."
    
    # Override: Engineering auto-approve up to $5K if all lines are Cloud (5020) or Software (5010)
    if dept == "Engineering" and total <= Decimal("5000"):
        if all(code in ("5010", "5020") for code in gl_codes):
            return "auto_approve: Engineering auto-approve up to $5,000 (all Cloud/Software)."
    
    # Base thresholds
    if total <= Decimal("1000"):
        return "auto_approve: Invoice total ≤ $1,000."
    elif total <= Decimal("10000"):
        return f"dept_manager: Invoice total ${total} requires department manager approval."
    elif total > Decimal("10000"):
        return f"vp_finance: Invoice total ${total} exceeds $10,000. Requires VP Finance approval."
    
    return "denied: No matching approval rule. Fail closed."
```

### Tool 6: `build_journal_entries`

```python
@ap_agent.tool_plain
def build_journal_entries(
    invoice_id: str,
    invoice_total: str,
    entries_json: str,
) -> str:
    """
    Build balanced double-entry journal entries from the agent's classifications.
    Call this as the FINAL step after approval.
    
    Args:
        invoice_id: The invoice ID (e.g., "INV-001")
        invoice_total: The invoice total amount (e.g., "24000.00")
        entries_json: JSON array of entry objects, each with:
                      {"debit": "GL code", "credit": "GL code", "amount": "decimal string", 
                       "description": "what this entry is for"}
    
    Returns: JSON with the complete journal entry set and verification result.
    """
    entries = json.loads(entries_json)
    total = Decimal(invoice_total)
    
    # Verify: sum of all entries crediting AP 2000 must equal invoice total
    ap_credits = sum(
        Decimal(e["amount"]) for e in entries 
        if e["credit"] == "2000"
    )
    
    verified = ap_credits == total
    
    return json.dumps({
        "invoice_id": invoice_id,
        "entries": entries,
        "verification": {
            "ap_credits_total": str(ap_credits),
            "invoice_total": str(total),
            "balanced": verified,
        },
    })
```

### Tool 7: `save_to_db`

```python
@ap_agent.tool
def save_to_db(ctx: RunContext[AgentDeps], journal_entries_json: str) -> str:
    """
    Persist journal entries to the database. Only call this in normal mode 
    (not dry-run, not shadow). Call this as the very last step.
    
    Args:
        journal_entries_json: The complete journal entries JSON from build_journal_entries.
    
    Returns: Confirmation message.
    """
    if ctx.deps.dry_run:
        return "DRY RUN: Entries NOT saved. Showing what would be posted."
    if ctx.deps.shadow:
        return "SHADOW MODE: Entries NOT saved. Proposal recorded for review."
    
    entries = json.loads(journal_entries_json)
    
    # Check idempotency
    if ctx.deps.store.has_journal_entries(ctx.deps.invoice.id):
        return f"ALREADY POSTED: Invoice {ctx.deps.invoice.id} has existing journal entries. Skipping."
    
    ctx.deps.store.save_journal_entries(ctx.deps.invoice.id, entries)
    return f"SUCCESS: {len(entries['entries'])} journal entries saved for {ctx.deps.invoice.id}."
```

---

## 6. Structured Output Model

The agent's final output is a Pydantic model that captures the complete processing result:

```python
from pydantic import BaseModel
from typing import Literal


class ClassifiedLine(BaseModel):
    line_index: int
    description: str
    amount: str
    gl_code: str
    gl_account_name: str
    treatment: Literal["expense", "prepaid", "accrual", "capitalize"]
    rule_applied: str  # The agent's reasoning for this classification


class ProcessingResult(BaseModel):
    invoice_id: str
    status: Literal["posted", "pending_approval", "flagged", "denied", "dry_run", "shadow"]
    po_match: Literal["matched", "no_po", "unmatched", "variance_exceeded"]
    classifications: list[ClassifiedLine]
    approval_level: str | None = None
    approval_reason: str | None = None
    journal_entries: list[dict] | None = None
    verification_passed: bool | None = None
    errors: list[str] = []
    warnings: list[str] = []
```

**Why this matters:** Because PydanticAI validates this model, the LLM cannot return garbage. If it hallucinates a GL code, the Pydantic validator rejects it, and PydanticAI retries with the error message. This gives us the safety of a deterministic system with the flexibility of an LLM.

---

## 7. The Engine (`src/agent.py` — Invocation)

The CLI invokes the agent synchronously:

```python
def process_invoice(invoice: Invoice, store: Store, dry_run: bool = False, shadow: bool = False) -> ProcessingResult:
    """Process a single invoice through the AP Agent."""
    deps = AgentDeps(
        invoice=invoice,
        po_store=load_purchase_orders(),
        store=store,
        dry_run=dry_run,
        shadow=shadow,
    )
    
    prompt = (
        f"Process this invoice:\n"
        f"Invoice ID: {invoice.id}\n"
        f"Vendor: {invoice.vendor}\n"
        f"Department: {invoice.department}\n"
        f"Date: {invoice.date}\n"
        f"Total: ${invoice.total}\n"
        f"PO: {invoice.po_number or 'NONE'}\n\n"
        f"Follow the SOP exactly. Start with Step 1 (PO Matching)."
    )
    
    if dry_run:
        prompt += "\n\nThis is a DRY RUN. Do NOT call save_to_db. Show what would be posted."
    elif shadow:
        prompt += "\n\nThis is SHADOW MODE. Do NOT call save_to_db. Produce a proposal for human review."
    
    result = ap_agent.run_sync(prompt, deps=deps)
    return result.output
```

This is the **entire orchestration logic**. No `engine.py` with step-by-step function calls. The LLM reads the prompt, reads its SOP, and decides what tools to call and in what order. The tools do the math. PydanticAI validates the output.

---

## 8. HITL Flow

### How Approval Pausing Works

When the agent determines that approval is required (via the `check_approval` tool), it sets `status = "pending_approval"` in its structured output. The CLI handles the pause:

```python
# In main.py — process command
result = process_invoice(invoice, store)

if result.status == "pending_approval":
    # Serialize the full result to the pending_approvals table
    store.save_pending(invoice.id, result.model_dump_json())
    click.echo(f"⏸ Invoice {invoice.id} requires {result.approval_level} approval.")
    click.echo(f"  Reason: {result.approval_reason}")
    click.echo(f"  Run: gl resume {invoice.id} --approve")
    click.echo(f"       gl resume {invoice.id} --reject")

elif result.status == "posted":
    click.echo(f"✅ Invoice {invoice.id} posted. {len(result.journal_entries)} entries saved.")
```

### How Resume Works

```python
# In main.py — resume command
@cli.command()
@click.argument("invoice_id")
@click.option("--approve/--reject", required=True)
def resume(invoice_id: str, approve: bool):
    """Resume a pending invoice approval."""
    store = Store()
    pending_json = store.get_pending(invoice_id)
    if not pending_json:
        click.echo(f"No pending approval found for {invoice_id}")
        return
    
    pending = ProcessingResult.model_validate_json(pending_json)
    
    if approve:
        # Re-invoke the agent with the approval decision pre-loaded
        # We already have the classifications — just need to post
        invoice = load_invoice(invoice_id)
        deps = AgentDeps(
            invoice=invoice,
            po_store=load_purchase_orders(),
            store=store,
        )
        
        result = ap_agent.run_sync(
            f"Invoice {invoice_id} has been APPROVED by {pending.approval_level}. "
            f"The classifications are: {json.dumps([c.model_dump() for c in pending.classifications])}. "
            f"Call build_journal_entries and then save_to_db to complete the posting.",
            deps=deps,
        )
        store.delete_pending(invoice_id)
        click.echo(f"✅ Invoice {invoice_id} approved and posted.")
    else:
        store.delete_pending(invoice_id)
        click.echo(f"❌ Invoice {invoice_id} rejected.")
```

---

## 9. Eval System

The eval system is nearly identical to the previous design. It runs the agent against all 6 labeled invoices and compares the output against ground truth:

```python
# eval/harness.py

def run_eval(store: Store) -> EvalReport:
    """Run the agent on all 6 labeled invoices and measure accuracy."""
    invoices = load_invoices(labeled_only=True)
    results = []
    
    for invoice in invoices:
        result = process_invoice(invoice, store, dry_run=True)
        expected = EXPECTED[invoice.id]
        
        comparison = compare_result(result, expected)
        results.append(comparison)
    
    return EvalReport(
        total=len(results),
        classification_accuracy=calc_accuracy(results, "gl_code"),
        treatment_accuracy=calc_accuracy(results, "treatment"),
        approval_accuracy=calc_accuracy(results, "approval"),
    )
```

**Key insight:** Because the agent returns a `ProcessingResult` with typed fields, the eval harness doesn't care whether the agent is powered by an LLM or regex. It just compares `result.classifications[0].gl_code` against `expected.gl_code`. This means the eval system is completely decoupled from the agent implementation.

---

## 10. Feedback Loop (The Agentic Self-Improvement)

This is where the agent architecture proves its superiority over the deterministic approach.

### The Workflow

```
1. Run eval → agent gets 5/6 invoices right (83% accuracy)
2. Human reviews: INV-002 Line 1 was classified as 5030, should be 5040
3. Human runs: gl feedback correct INV-002 1 --gl 5040 --reason "advisory, not legal"
4. Correction saved to SQLite corrections table
5. Human runs: gl feedback rerun
6. Agent is re-invoked. This time, build_system_prompt() queries the corrections table
   and injects: "- INV-002 Line 1: Was 5030, should be 5040. Reason: advisory, not legal"
   into the system prompt as a few-shot example
7. Agent reads the correction, applies it, classifies correctly → 100% accuracy
8. CLI shows: "Before: 83% → After: 100%"
```

### Why This Is Genuinely Agentic

In the deterministic design, "feedback" meant a human developer editing regex rules manually. In the agent design, the feedback loop **directly modifies the agent's behavior** by injecting corrections into its prompt. The agent literally learns from corrections without any code changes. This is a genuine, demonstrable, agentic self-improvement cycle.

---

## 11. Shadow Mode

Shadow mode processes the 10 unlabeled invoices in dry-run mode and produces proposals:

```python
# In main.py — shadow command
@cli.command()
def shadow():
    """Process unlabeled invoices in shadow mode for human review."""
    store = Store()
    invoices = load_invoices(unlabeled_only=True)
    
    for invoice in invoices:
        result = process_invoice(invoice, store, shadow=True)
        click.echo(format_shadow_result(result))
```

The agent processes each unlabeled invoice exactly like a labeled one, but with `shadow=True` which prevents `save_to_db` from persisting. The output includes the agent's reasoning (`rule_applied` field) for each classification, which the human reviewer uses to validate or correct.

---

## 12. Testing Strategy

### Unit Tests (Deterministic Tools)

```python
# tests/test_tools.py
def test_amortization_12_months():
    result = calculate_amortization("24000.00", "1310", "5010", "2026-01-01", "2026-12-31")
    schedule = json.loads(result)
    assert schedule["treatment"] == "prepaid"
    assert len(schedule["monthly_entries"]) == 12
    assert all(Decimal(e["amount"]) == Decimal("2000.00") for e in schedule["monthly_entries"])

def test_accrual_detection():
    result = check_accrual("2026-01-15", "2025-12-31", "7500.00", True)
    data = json.loads(result)
    assert data["treatment"] == "accrual"
    assert data["accrual_account"] == "2110"

def test_approval_fixed_asset_overrides():
    # ... test that 1500 always routes to VP Finance
```

### Agent Tests (PydanticAI TestModel)

PydanticAI provides a `TestModel` that returns pre-configured responses, allowing fully deterministic agent tests without calling an LLM:

```python
# tests/test_agent.py
from pydantic_ai.models.test import TestModel

def test_agent_returns_valid_result():
    """Test that the agent produces a valid ProcessingResult structure."""
    with ap_agent.override(model=TestModel()):
        result = process_invoice(sample_invoice, test_store, dry_run=True)
        assert isinstance(result, ProcessingResult)
        assert result.invoice_id == "INV-001"
```

### End-to-End Tests (Live LLM)

```python
# tests/test_e2e.py (requires API key, run manually)
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="No API key")
def test_inv001_full_pipeline():
    result = process_invoice(load_invoice("INV-001"), Store(":memory:"), dry_run=True)
    assert result.status == "dry_run"
    assert result.classifications[0].gl_code == "1310"
    assert result.classifications[0].treatment == "prepaid"
```

---

## 13. CLI Commands (Unchanged Surface Area)

```
gl process <invoice_id>              # Full pipeline via agent
gl process <invoice_id> --dry-run    # Agent processes but doesn't save
gl eval                              # Run agent on 6 labeled invoices, show accuracy
gl shadow                            # Agent processes 10 unlabeled invoices
gl resume <invoice_id> --approve     # Resume pending approval
gl resume <invoice_id> --reject
gl feedback correct <id> <line> --gl <code> --reason <text>
gl feedback analyze                  # Show error patterns from corrections DB
gl feedback rerun                    # Before/after accuracy comparison
```

---

## 14. Known Spec Ambiguities & Resolutions

All ambiguity resolutions from the previous DESIGN.md (§1b) remain valid and unchanged:

- **A1.** INV-002 Line 2: "Regulatory compliance advisory" → 5040 (service type is advisory, not legal action). This is embedded in the system prompt's classification rules.
- **A2.** INV-004: Expected GL shows post-recognition accrual codes (2110/2100). Eval compares at `posting_gl` level.
- **A3.** Prepaid GL codes (1310, 1300) ≠ expense codes (5010, 5020) for Engineering auto-approve override. Override correctly checks classifier GL.

---

## 15. Validation Checklist (What "Done" Looks Like)

- [ ] `uv run gl process INV-001 --dry-run` → Agent calls `match_po`, classifies as 1310 (prepaid), calls `calculate_amortization`, shows 12 monthly entries
- [ ] `uv run gl process INV-002 --dry-run` → Agent classifies 3 lines as 5030/5040/5030 with correct reasoning
- [ ] `uv run gl process INV-003 --dry-run` → Agent classifies 5110 (laptops), 1500 (server), 1300 (cloud prepaid)
- [ ] `uv run gl process INV-004 --dry-run` → Agent detects accrual condition, calls `check_accrual`, produces 2110/2100 entries
- [ ] `uv run gl process INV-005 --dry-run` → Agent applies Rule 1 priority for branded merch → 5000, rest → 5050
- [ ] `uv run gl process INV-006 --dry-run` → Agent detects no PO, flags for review, does NOT classify
- [ ] `uv run gl eval` → Shows ≥95% accuracy across classification, treatment, and approval
- [ ] `uv run gl shadow` → Produces proposals for 10 unlabeled invoices with agent reasoning
- [ ] `uv run gl feedback correct` + `uv run gl feedback rerun` → Shows measurable accuracy improvement
- [ ] `uv run gl resume` → HITL flow works with state persistence
- [ ] All tool unit tests pass (`uv run pytest tests/test_tools.py`)
- [ ] README documents architecture, agent design, and tradeoffs
