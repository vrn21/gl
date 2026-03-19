# gl — Accounts Payable AI Agent

An AI agent that fully automates an Accounts Payable workflow. Given a vendor invoice, the agent matches it to a purchase order, classifies every line item to the correct General Ledger account, detects prepaid amortizations and accrual conditions, routes the invoice through the appropriate approval path, and posts balanced double-entry journal entries — all following a strict Standard Operating Procedure.

The LLM is the brain. It reads the company's SOP, reasons about each invoice line-by-line, and calls deterministic Python tools for exact math, lookups, and state mutations. Humans stay in the loop for approval decisions and accuracy corrections.

---

## Table of Contents

- [Architecture](#architecture)
- [Setup](#setup)
- [End-to-End Workflow](#end-to-end-workflow)
  - [Processing an Invoice](#1-processing-an-invoice)
  - [Dry-Run Mode](#2-dry-run-mode)
  - [Human-in-the-Loop Approval](#3-human-in-the-loop-approval)
  - [Evaluation](#4-evaluation)
  - [Shadow Mode](#5-shadow-mode)
  - [Feedback Loop](#6-feedback-loop--self-improvement)
- [How Classification Works](#how-classification-works)
- [Chart of Accounts](#chart-of-accounts)
- [Design Decisions & Tradeoffs](#design-decisions--tradeoffs)
- [Scaling to Production](#scaling-to-production-bottlenecks--mitigations)
- [Project Structure](#project-structure)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AP Agent (LLM Brain)                        │
│                                                                    │
│   System Prompt:  Full SOP  +  Chart of Accounts                   │
│                   + Few-Shot Corrections (from feedback DB)         │
│                                                                    │
│   The agent reads each invoice, reasons about every line item,     │
│   and calls tools in the correct SOP order:                        │
│                                                                    │
│   ┌────────────────┐   ┌────────────────────┐   ┌───────────────┐  │
│   │   match_po()   │   │ get_invoice_details │   │ check_accrual │  │
│   │   PO Store     │   │    Invoice JSON     │   │  Date Math    │  │
│   └────────────────┘   └────────────────────┘   └───────────────┘  │
│                                                                    │
│   ┌──────────────────────┐  ┌─────────────────┐  ┌──────────────┐  │
│   │ calculate_amortize() │  │ check_approval() │  │ save_to_db() │  │
│   │   Decimal Math       │  │  Threshold Logic │  │   SQLite     │  │
│   └──────────────────────┘  └─────────────────┘  └──────────────┘  │
│                                                                    │
│   ┌──────────────────────────┐                                     │
│   │ build_journal_entries()  │ ──→ Balanced double-entry JEs       │
│   │   Debit == Credit check  │                                     │
│   └──────────────────────────┘                                     │
└───────────────────────────────────┬─────────────────────────────────┘
                                    │
                                    ▼
                          Structured Output:
                        ProcessingResult (Pydantic)
```

### Why This Architecture

| Design Choice | Rationale |
| --- | --- |
| **LLM as orchestrator** | The agent reads the SOP from its system prompt and decides which tool to call next. No hardcoded pipeline — the LLM reasons through the workflow. |
| **Deterministic tools for math** | All money calculations (amortization schedules, accrual entries, journal balancing) use Python `Decimal`. The LLM never does arithmetic — it calls tools. |
| **PydanticAI framework** | Structured output via `output_type=ProcessingResult` — the LLM's response is validated by Pydantic. Invalid GL codes or malformed entries trigger automatic retry with the validation error. |
| **SQLite for state** | Journal entries, pending approvals, and feedback corrections are persisted locally. No external database required. |

---

## Setup

### Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager
- An **OpenAI API key** (or any LLM provider supported by PydanticAI)


### Configuration

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-your-key-here
```

The agent uses `gpt-4o-mini` by default. The model is configured in `src/agent.py` and can be swapped to any PydanticAI-supported provider (Anthropic, Google, etc.) without code changes.

### Verify Installation

```bash
uv run gl --help
```

Expected output:

```
Usage: gl [OPTIONS] COMMAND [ARGS]...

  gl — Accounts Payable Agent

Options:
  --help  Show this message and exit.

Commands:
  eval      Run evaluation against all 6 labeled invoices.
  feedback  Feedback loop: corrections, analysis, and re-evaluation.
  process   Process a single invoice through the AP pipeline.
  resume    Resume a pending invoice approval.
  shadow    Process 10 unlabeled invoices in shadow mode for human review.
```

---

## End-to-End Workflow

### 1. Processing an Invoice

Process a single invoice through the full 5-step SOP pipeline:

```bash
uv run gl process INV-001
```

The agent will:

1. **Match the PO** — verify `PO-2026-044` exists and the amount is within 10% tolerance
2. **Classify each line item** — determine the GL account (e.g., `1310` for annual software prepayment)
3. **Detect prepaid/accrual** — for the annual license (Jan–Dec 2026), call `calculate_amortization` to produce a 12-month schedule amortizing $2,000/mo from 1310 → 5010
4. **Check approval** — invoice total $24,000 > $10K threshold → requires VP Finance approval
5. **Pause for human approval** — serialize state and prompt for `gl resume`

```
══════════════════════════════════════════════════
Invoice: INV-001  |  Status: pending_approval
══════════════════════════════════════════════════
PO Match: matched
  Line 0: Annual Platform License (Jan–Dec 2026)
    → 1310 (prepaid)  [Software annual prepayment → 1310, amortize to 5010]
Approval: vp_finance — Invoice total $24000 exceeds $10,000

⏸ Invoice INV-001 requires vp_finance approval.
  Run: gl resume INV-001 --approve
       gl resume INV-001 --reject
```

### 2. Dry-Run Mode

Preview the full processing result without persisting anything to the database:

```bash
uv run gl process INV-002 --dry-run
```

This runs the agent identically but prevents the `save_to_db` tool from writing. Useful for testing and validation.

### 3. Human-in-the-Loop Approval

When an invoice requires manager or VP approval, the system pauses and serializes the full processing state. A human reviewer can then approve or reject:

```bash
# Approve and post journal entries
uv run gl resume INV-001 --approve

# Reject the invoice
uv run gl resume INV-001 --reject
```

On approval, the agent is re-invoked with the pre-computed classifications to build and persist journal entries. On rejection, the pending state is cleared.

**The full HITL flow:**

```
Process Invoice ─→ Agent classifies & routes
                        │
                   Approval needed?
                   ┌────┴────┐
                   No        Yes
                   │         │
                   ▼         ▼
              Post JEs   Serialize to DB
                         ("pending_approvals")
                              │
                         Human reviews
                         ┌────┴────┐
                     Approve     Reject
                         │         │
                         ▼         ▼
                    Post JEs   Clear state
```

### 4. Evaluation

Run the agent against all 6 labeled invoices and measure accuracy across three dimensions:

```bash
uv run gl eval
```

Sample output:

```
═══ Evaluation Report ═══
Overall: 28/37 correct (75.7%)

Posting GL: 12/13 line items correct (92.3%)
  ✓ INV-001: Annual Platform License → 1310 (prepaid)
  ✓ INV-004: Operational efficiency assessment → 2110 (accrual)
  ✓ INV-004: Travel expenses → 2100 (accrual)
  ✓ INV-006: flagged (no PO) — correctly did NOT classify

Treatment: 5/13 correct (38.5%)
Approval: 6/6 correct (100.0%)
  ✓ INV-001: vp_finance
  ✓ INV-002: dept_manager
  ✓ INV-003: vp_finance
  ✓ INV-004: dept_manager
  ✓ INV-005: vp_finance
  ✓ INV-006: flagged (no PO)
Journal Verification: 5/5 passed
```

The eval system measures:

| Metric | What It Checks |
| --- | --- |
| **Posting GL** | Did the agent assign the correct final GL account code? |
| **Treatment** | Did it correctly identify expense vs. prepaid vs. accrual vs. capitalize? |
| **Approval** | Did it route to the correct approval level (auto / dept_manager / vp_finance)? |
| **Journal Verification** | Do debits equal credits in every journal entry? |

### 5. Shadow Mode

Process the 10 unlabeled invoices without posting, generating proposals for human review:

```bash
uv run gl shadow
```

Each unlabeled invoice (UL-1 through UL-10) is processed through the full pipeline with `shadow=True`, which prevents database writes. The output includes the agent's classification reasoning for every line item, allowing a human reviewer to validate or correct.

### 6. Feedback Loop & Self-Improvement

This is where the agent architecture demonstrates its value. When the agent makes mistakes, human corrections are collected and **directly improve the agent's future accuracy**.

#### Step 1: Record a Correction

```bash
uv run gl feedback correct INV-005 0 \
  --gl 5050 \
  --treatment expense \
  --reason "Digital ad campaigns are simple monthly expenses, not prepaid."
```

#### Step 2: Analyze Error Patterns

```bash
uv run gl feedback analyze
```

Shows which GL codes are most commonly corrected and identifies systematic misclassification patterns.

#### Step 3: Re-Run Evaluation (Before/After)

```bash
uv run gl feedback rerun
```

This re-runs the full evaluation with corrections injected into the agent's system prompt as few-shot examples. The output shows a before/after accuracy comparison:

```
═══ Before/After Comparison ═══
Before: 75.7% overall accuracy
After:  94.6% overall accuracy  (+18.9%)
```

**How it works internally:**

```
Corrections DB ──→ build_system_prompt()
                        │
                   Injects few-shot examples:
                   "INV-005 Line 0: Was 1310, should be 5050.
                    Reason: ad campaigns are monthly expenses."
                        │
                   Agent reads these on next invocation
                        │
                   Applies learned corrections ──→ Higher accuracy
```

The agent literally learns from its mistakes without any code changes. Corrections are persisted in SQLite and dynamically injected into the system prompt on every run.

---

## How Classification Works

The agent follows a strict **priority-ordered** classification ruleset. The first matching rule wins:

```
Priority 1: Physical Goods
  └─ Office supplies, stationery, toner → 5000
  └─ Branded merchandise (t-shirts, swag, gift bags) → 5000
     (even if from Marketing department)

Priority 2: Equipment
  └─ Unit cost < $5,000 → 5110 (expense)
  └─ Unit cost ≥ $5,000 → 1500 (capitalize as fixed asset)

Priority 3: Software/SaaS
  └─ Monthly or usage-based → 5010
  └─ Annual prepayment → 1310 (prepaid, amortize monthly to 5010)

Priority 4: Cloud Hosting
  └─ Monthly → 5020
  └─ Annual prepayment → 1300 (prepaid, amortize monthly to 5020)

Priority 5: Professional Services
  └─ Legal (litigation, patent filing, contract drafting) → 5030
  └─ Consulting/advisory (assessment, analysis, review) → 5040
  └─ Mixed legal + non-legal → 5030 (conservative)

Priority 6: Marketing
  └─ Ad spend, campaigns, sponsorships, booth rental → 5050
  └─ Exception: branded physical merchandise → 5000 (Priority 1 wins)

Priority 7: Other Categories
  └─ Travel & meals → 5060
  └─ Facilities & maintenance → 5070
  └─ Training & conferences → 5080
  └─ Telecom & internet → 5090
  └─ Insurance (annual) → 1320 (prepaid, amortize to 5100)
```

### Prepaid & Accrual Recognition

After classification, the agent detects two special treatments:

- **Prepaid:** Service period > 1 month AND paid upfront → book to prepaid account, call `calculate_amortization` to produce a monthly amortization schedule to the corresponding expense account.
- **Accrual:** Invoice date is AFTER the service period ended → book accrual entry (2110 for professional services, 2100 for all others), plus a reversal entry.

### Approval Routing

| Condition | Approval Level |
| --- | --- |
| Invoice total ≤ $1,000 | Auto-approve |
| $1,000 < total ≤ $10,000 | Department Manager |
| Total > $10,000 | VP Finance |
| Any line classified as Fixed Asset (1500) | VP Finance (regardless of amount) |
| Marketing dept, total ≤ $2,500 | Auto-approve (override) |
| Engineering dept, total ≤ $5,000, all lines Cloud/Software | Auto-approve (override) |
| No matching rule | Denied (fail closed) |

---

## Chart of Accounts

| Code | Account | Category |
| --- | --- | --- |
| 1300 | Prepaid Expenses (General) | Prepaid |
| 1310 | Prepaid Software | Prepaid |
| 1320 | Prepaid Insurance | Prepaid |
| 1500 | Fixed Assets | Asset |
| 2000 | Accounts Payable | Liability |
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

---

## Design Decisions & Tradeoffs

### 1. LLM as Orchestrator, Not Calculator

The most critical design decision. The LLM reads the full SOP and **decides** which tools to call and in what order — it's the "brain" of the pipeline. But it **never** performs arithmetic. All money math (amortization schedules, accrual entries, journal balancing, approval threshold checks) is handled by deterministic Python tools using `Decimal` precision.

**Why:** LLMs hallucinate numbers. By delegating math to tools, we get the semantic reasoning power of the LLM with the precision guarantees of deterministic code.

### 2. PydanticAI over LangGraph

We chose [PydanticAI](https://ai.pydantic.dev/) for agent construction instead of LangGraph or raw OpenAI function calling:

- **Structured output validation** — `output_type=ProcessingResult` means the LLM's response is automatically validated by Pydantic. Malformed output triggers a retry with the validation error.
- **Dependency injection** — `AgentDeps` cleanly provides the invoice, PO store, and DB connection to every tool via `RunContext`.
- **Minimal boilerplate** — `@agent.tool` decorator with Python docstrings as tool descriptions. No graph DSL, no node wiring.
- **Testability** — `TestModel` allows fully deterministic agent tests without calling the LLM.

**Tradeoff:** PydanticAI is simpler but less powerful than LangGraph for complex multi-agent DAGs. Our workflow is fundamentally linear (process one invoice at a time), so the simplicity wins.

### 3. Feedback as Prompt Engineering, not Retraining

When humans correct the agent's mistakes, corrections are stored in SQLite and injected into the system prompt as few-shot examples on the next run. The agent literally learns from corrections without changing any code or retraining any model.

**Tradeoff:** This approach has a context window limit — thousands of corrections would bloat the prompt. For this scale (6 labeled + 10 unlabeled invoices), it works well and is far simpler than fine-tuning.

### 4. Externalized SOP

The Standard Operating Procedure (classification rules, approval thresholds, chart of accounts) lives in `data/sop.md` — a plain markdown file loaded at runtime by the prompt builder. Updating the SOP is a config change, not a code change:

```bash
# Edit the SOP
vim data/sop.md

# That's it — the agent reads the updated SOP on the next invocation.
# No code changes, no redeployment.
```

### 5. SQLite for Everything

- Journal entries, pending approvals, and corrections all live in a single `gl.db` file
- No migration framework — tables are created on first run via `CREATE TABLE IF NOT EXISTS`
- Append-only journal entries with idempotency checks (no double-posting)

**Tradeoff:** Not suitable for concurrent writes or multi-user environments. Fine for a single-agent CLI tool.

### 6. State-File HITL over Interactive Prompts

When the agent determines that approval is required, it serializes the full `ProcessingResult` to a `pending_approvals` table and exits cleanly. The `gl resume` command loads the state and continues. This is testable, scriptable, and doesn't block the terminal.

---

## Scaling to Production: Bottlenecks & Mitigations

This system is built for correctness at small scale. Moving to production (hundreds of invoices/day, multiple users, SLA requirements) would surface the following bottlenecks:

### LLM Latency & Cost

| Concern | Detail |
| --- | --- |
| **Latency** | Each invoice requires a full LLM round-trip (typically 3–8 seconds with `gpt-4o-mini`). At 500 invoices/day, that's ~40 minutes of serial processing time. |
| **Cost** | Each invocation consumes ~2K tokens (system prompt) + ~500 tokens (invoice + tool calls). At ~$0.15/1M input tokens, this is negligible per-invoice, but the system prompt is repeated on every call. |
| **Non-determinism** | The same invoice may produce slightly different classifications across runs. Temperature is not set to 0 by default. |

**Mitigations:**
- Set `temperature=0` for reproducible classifications
- Batch invoices and process them in parallel with `asyncio.gather()` (PydanticAI supports async natively)
- Cache classifications for previously-seen invoice line descriptions to skip LLM calls entirely
- For very high volume, consider a hybrid approach: use the LLM only for ambiguous line items and fall back to deterministic rules for clear-cut cases (e.g., "AWS" → always 5020)

### SQLite Concurrency

SQLite uses file-level locking. A single writer blocks all other writers. This works for a CLI tool processing one invoice at a time but breaks under concurrent access.

**Mitigations:**
- Switch to PostgreSQL for multi-user production deployments
- Use WAL (Write-Ahead Logging) mode for SQLite to allow concurrent reads during writes: `PRAGMA journal_mode=WAL`
- The `Store` class is already abstracted — swapping the backend requires changing only `store.py`

### Feedback Loop Context Window Ceiling

Corrections are injected as few-shot examples into the system prompt. With thousands of corrections, the prompt exceeds the model's context window.

**Mitigations:**
- Cap the number of injected corrections to the N most recent or most relevant (e.g., same GL code, same vendor)
- Periodically "graduate" corrections into the SOP itself — if the same correction appears 5+ times, update `data/sop.md` with the new rule and archive the corrections
- For enterprise scale, fine-tune a model on the correction dataset instead of few-shotting

### Approval State Durability

Pending approvals are stored in SQLite. If the database file is lost or corrupted, all pending approvals are lost.

**Mitigations:**
- Regular database backups (or use a managed database)
- Add a `gl pending list` command to audit all pending invoices
- In production, integrate with an external approval system (Slack, email, or an ERP) rather than relying on CLI-based `resume`

### Observability & Audit Trail

The current system logs results to stdout. There's no structured logging, no tracing of which LLM calls were made, and no audit trail of who approved what.

**Mitigations:**
- Add structured JSON logging with timestamps, invoice IDs, and tool call traces
- PydanticAI supports [Logfire](https://logfire.pydantic.dev/) integration for tracing every LLM call, tool invocation, and retry
- Store the full `ProcessingResult` (including the agent's reasoning in `rule_applied`) as an audit record alongside journal entries

### Input Validation & Document Parsing

The system currently requires pre-structured JSON invoices. Real-world invoices arrive as PDFs, emails, or scanned images.

**Mitigations:**
- Add a document parsing layer (e.g., AWS Textract, Google Document AI, or an open-source OCR pipeline) upstream of the agent
- Validate parsed output against the `Invoice` Pydantic model before passing to the agent — malformed invoices are rejected before any LLM cost is incurred

---

## Project Structure

```
gl/
├── main.py                     # CLI entry point (Click) — all commands
├── pyproject.toml              # Project metadata & dependencies
├── .env                        # API keys (gitignored)
│
├── src/
│   ├── agent.py                # PydanticAI agent definition + process_invoice()
│   ├── tools.py                # 7 deterministic tool functions the agent calls
│   ├── prompts.py              # Dynamic system prompt builder (SOP + few-shot)
│   ├── models.py               # Pydantic data models (Invoice, JournalEntry, etc.)
│   ├── chart_of_accounts.py    # GL account registry & lookups
│   ├── store.py                # SQLite persistence layer
│   └── fixtures.py             # JSON fixture loader (invoices & POs)
│
├── data/
│   ├── invoices.json           # 16 invoices (6 labeled + 10 unlabeled)
│   ├── purchase_orders.json    # Purchase order fixtures
│   └── sop.md                  # The SOP — editable without touching code
│
├── eval/
│   ├── expected.py             # Ground truth for 6 labeled invoices
│   ├── runner.py               # Eval harness & shadow mode runner
│   ├── report.py               # Report formatting
│   └── feedback.py             # Correction storage, analysis, before/after
│
└── tests/
    ├── test_tools.py           # Unit tests for deterministic tools
    ├── test_agent.py           # Agent tests with PydanticAI TestModel
    └── test_e2e.py             # End-to-end tests (requires API key)
```

---

## Running Tests

```bash
# Unit tests (no API key needed — uses PydanticAI TestModel)
uv run pytest tests/test_tools.py tests/test_agent.py

# End-to-end tests (requires OPENAI_API_KEY in .env)
uv run pytest tests/test_e2e.py

# All tests
uv run pytest
```

---

## Quick Reference

| Command | Description |
| --- | --- |
| `gl process <id>` | Process an invoice through the full SOP pipeline |
| `gl process <id> --dry-run` | Preview without posting to the database |
| `gl eval` | Run accuracy evaluation on all 6 labeled invoices |
| `gl shadow` | Process 10 unlabeled invoices as proposals for review |
| `gl resume <id> --approve` | Approve a pending invoice and post journal entries |
| `gl resume <id> --reject` | Reject a pending invoice |
| `gl feedback correct <id> <line> --gl <code>` | Record a human correction |
| `gl feedback analyze` | Show error patterns from collected corrections |
| `gl feedback rerun` | Re-evaluate and show before/after accuracy improvement |
