# Phase 4 — CLI, HITL & Polish

> **Depends on:** Phase 1 (models, fixtures, store) + Phase 2 (pipeline, engine) + Phase 3 (eval, shadow, feedback)
> **Produces:** Full CLI, human-in-the-loop flow, end-to-end tests, README.
> **Estimated effort:** ~4 hours

---

## Goal

Wire everything together: CLI entry point, HITL pause/resume flow, comprehensive end-to-end tests, and a README documenting architecture, setup, and tradeoffs. After this phase, the system is a complete, shippable deliverable.

---

## 1. CLI Entry Point (`main.py`)

### Framework: Click

Use **Click** (already in `pyproject.toml` from Phase 1). Not Typer — Click is zero-magic, no async complexity.

### Command Structure

```python
import click
from pathlib import Path
from src.engine import Engine
from src.store import Store
from src.fixtures import load_invoices, load_invoice, load_purchase_orders

@click.group()
def cli():
    """gl — Accounts Payable Agent"""
    pass

# ─── Process Command ───

@cli.command()
@click.argument("invoice_id")
@click.option("--dry-run", is_flag=True, help="Show what would be posted without posting")
def process(invoice_id: str, dry_run: bool):
    """Process a single invoice through the AP pipeline."""
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoice = load_invoice(invoice_id)
    result = engine.process(invoice, dry_run=dry_run)
    _print_result(result)

# ─── Eval Command ───

@cli.command()
def eval():
    """Run evaluation against all 6 labeled invoices."""
    from eval.runner import run_eval
    from eval.report import format_report
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoices = [inv for inv in load_invoices() if inv.id.startswith("INV-")]
    report = run_eval(engine, invoices)
    click.echo(format_report(report))

# ─── Shadow Command ───

@cli.command()
def shadow():
    """Process 10 unlabeled invoices in shadow mode."""
    from eval.runner import run_shadow
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoices = load_invoices()
    results = run_shadow(engine, invoices)
    _print_shadow_report(results)

# ─── Resume Command ───

@cli.command()
@click.argument("invoice_id")
@click.option("--approve", "decision", flag_value="approve", help="Approve the pending invoice")
@click.option("--reject", "decision", flag_value="reject", help="Reject the pending invoice")
def resume(invoice_id: str, decision: str):
    """Resume a pending-approval or denied invoice."""
    if decision is None:
        click.echo("Error: must specify --approve or --reject", err=True)
        raise SystemExit(1)
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    result = engine.resume(invoice_id, approved=(decision == "approve"))
    _print_result(result)

# ─── Feedback Command Group ───

@cli.group()
def feedback():
    """Feedback loop: corrections, analysis, and re-evaluation."""
    pass

@feedback.command()
@click.argument("invoice_id")
@click.argument("line_index", type=int)
@click.option("--gl", required=True, help="Corrected GL code")
@click.option("--treatment", default=None, help="Corrected treatment (expense/prepaid/accrual/capitalize)")
@click.option("--reason", default=None, help="Reason for correction")
def correct(invoice_id: str, line_index: int, gl: str, treatment: str | None, reason: str | None):
    """Record a correction for a misclassified line item."""
    from eval.feedback import save_correction
    store = Store()
    save_correction(store, invoice_id, line_index, gl, treatment, reason)
    click.echo(f"Correction saved: {invoice_id} line {line_index} → {gl}")

@feedback.command()
def analyze():
    """Show error patterns from corrections."""
    from eval.feedback import analyze_corrections
    store = Store()
    click.echo(analyze_corrections(store))

@feedback.command()
def rerun():
    """Re-run eval and show before/after accuracy comparison."""
    from eval.feedback import rerun_with_comparison
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoices = load_invoices()
    click.echo(rerun_with_comparison(engine, invoices, store))

if __name__ == "__main__":
    cli()
```

### Result Display Helper

```python
def _print_result(result: ProcessingResult):
    """Print a ProcessingResult to stdout in human-readable format."""
    click.echo(f"\n{'═' * 50}")
    click.echo(f"Invoice: {result.invoice_id}  |  Status: {result.status}")
    click.echo(f"{'═' * 50}")

    if result.po_result:
        po = result.po_result
        click.echo(f"PO Match: {po.status}" +
                   (f" (variance: {po.variance_pct:.1%})" if po.variance_pct else ""))

    for i, cl in enumerate(result.classifications):
        if cl.classification:
            c = cl.classification
            posting = c.posting_gl or c.gl_code
            click.echo(f"  Line {i}: {cl.line_item.description}")
            click.echo(f"    → {posting} ({c.treatment.value})  [{c.rule_applied}]")

    if result.approval:
        click.echo(f"Approval: {result.approval.level.value} — {result.approval.reason}")

    if result.journal_entries:
        click.echo(f"\nJournal Entries ({len(result.journal_entries)}):")
        for entry in result.journal_entries:
            click.echo(f"  [{entry.entry_type}] {entry.date} — {entry.description}")
            for line in entry.lines:
                if line.debit > 0:
                    click.echo(f"    Dr  {line.account_code} {line.account_name:40s} {line.debit:>12}")
                if line.credit > 0:
                    click.echo(f"    Cr  {line.account_code} {line.account_name:40s} {' '*12}{line.credit:>12}")

    for err in result.errors:
        click.echo(f"  ⚠ {err}", err=True)
    for warn in result.warnings:
        click.echo(f"  ⓘ {warn}")
```

---

## 2. Human-in-the-Loop (HITL) Flow

### Design: State File + Resume Command

HITL is NOT interactive prompts. It's `save_pending()` + `resume`. This makes it testable, scriptable, and CI-friendly.

### Flow

```
$ gl process INV-003
═══════════════════════════════════════════════════
Invoice: INV-003  |  Status: pending_approval
═══════════════════════════════════════════════════
PO Match: matched
  Line 0: MacBook Pro 14" (3x $1,800)
    → 5110 (expense)
  Line 1: Dell PowerEdge R760 Server
    → 1500 (capitalize)
  Line 2: AWS Reserved Instances Annual
    → 1300 (prepaid)
Approval: vp_finance — Fixed Asset (1500) requires VP Finance approval

⏸ Invoice saved for approval. Run:
  gl resume INV-003 --approve
  gl resume INV-003 --reject

$ gl resume INV-003 --approve
═══════════════════════════════════════════════════
Invoice: INV-003  |  Status: posted
═══════════════════════════════════════════════════
✓ Journal entries posted (5 entries)
```

### Implementation Notes

1. When `engine.process()` returns `status="pending_approval"`, the CLI prints the pending notice with resume instructions.
2. `engine.resume()` loads the stored `ProcessingResult` from SQLite, posts if approved, rejects if rejected, and deletes from `pending_approvals`.
3. `resume` re-verifies journal entries before posting (guard against corrupt state).
4. **Denied invoices are also resumable.** `status="flagged"` for denied invoices also saves to pending, allowing human override of the fail-closed rule.

### What Gets Serialized

The full `ProcessingResult` is serialized via `result.model_dump_json()` and stored in `pending_approvals.result_json`. On resume, it's reconstructed via `ProcessingResult.model_validate_json(json_str)`.

Key fields that must survive serialization:
- `invoice_total` (for verification on resume)
- `journal_entries` (the actual entries to post)
- `approval` (to update `approved` field)
- `classifications` (for display)

---

## 3. End-to-End Tests (`tests/test_e2e.py`)

### Test Strategy

One test per labeled invoice, testing the full pipeline end-to-end. Use a temporary SQLite DB (`tmp_path` fixture). Process each invoice in `dry_run=True` mode and assert on the `ProcessingResult`.

### Test Cases

```python
import pytest
from decimal import Decimal
from src.engine import Engine
from src.store import Store
from src.fixtures import load_invoice, load_purchase_orders

@pytest.fixture
def engine(tmp_path):
    store = Store(db_path=tmp_path / "test.db")
    po_store = load_purchase_orders()
    return Engine(store=store, po_store=po_store)

def test_inv001_cloudware_prepaid(engine):
    """INV-001: $24K annual software license → 1310 prepaid + 12 amortization entries."""
    inv = load_invoice("INV-001")
    result = engine.process(inv, dry_run=True)

    assert result.status == "dry_run"
    assert len(result.classifications) == 1

    cl = result.classifications[0].classification
    assert cl.gl_code == "1310"
    assert cl.treatment.value == "prepaid"

    # 1 initial + 12 amortization = 13 entries
    assert len(result.journal_entries) == 13
    assert result.journal_entries[0].entry_type == "initial"
    assert all(e.entry_type == "amortization" for e in result.journal_entries[1:])

    # Amortization sum = $24,000
    amort_total = sum(
        l.debit for e in result.journal_entries[1:] for l in e.lines
        if l.account_code == "5010"
    )
    assert amort_total == Decimal("24000.00")

    assert result.approval.level.value == "vp_finance"

def test_inv002_morrison_burke_legal(engine):
    """INV-002: 3 professional service lines → 5030/5040/5030."""
    inv = load_invoice("INV-002")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    assert gls == ["5030", "5040", "5030"]
    assert all(cl.classification.treatment.value == "expense"
               for cl in result.classifications)
    assert result.approval.level.value == "dept_manager"

def test_inv003_techdirect_mixed(engine):
    """INV-003: Mixed invoice — laptops (5110), server (1500), AWS prepaid (1300)."""
    inv = load_invoice("INV-003")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    treatments = [cl.classification.treatment.value for cl in result.classifications]
    assert gls == ["5110", "1500", "1300"]
    assert treatments == ["expense", "capitalize", "prepaid"]
    assert result.approval.level.value == "vp_finance"  # Any 1500 → VP Finance

def test_inv004_apex_accrual(engine):
    """INV-004: Dec 2025 service billed Jan 2026 → accrual entries."""
    inv = load_invoice("INV-004")
    result = engine.process(inv, dry_run=True)

    # Both lines should be accrual
    for cl in result.classifications:
        assert cl.classification.treatment.value == "accrual"

    # Check posting_gl (accrual accounts set by recognition)
    posting_gls = [cl.classification.posting_gl for cl in result.classifications]
    assert posting_gls == ["2110", "2100"]

    # Each accrual line produces 2 entries (accrual + reversal) = 4 total
    assert len(result.journal_entries) == 4
    types = [e.entry_type for e in result.journal_entries]
    assert types.count("accrual") == 2
    assert types.count("reversal") == 2

def test_inv005_brightspark_branded_merch(engine):
    """INV-005: Branded merch → 5000, marketing → 5050. Merch exception tested."""
    inv = load_invoice("INV-005")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    assert gls == ["5050", "5000", "5050", "5000"]
    assert result.approval.level.value == "vp_finance"

def test_inv006_no_po_flagged(engine):
    """INV-006: No PO → flagged, no classifications, no approval."""
    inv = load_invoice("INV-006")
    result = engine.process(inv, dry_run=True)

    assert result.status == "flagged"
    assert len(result.classifications) == 0
    assert result.approval is None
    assert any("PO" in err for err in result.errors)
```

### HITL Resume Test

```python
def test_hitl_resume_approve(engine):
    """Test the full pending → approve → posted flow."""
    inv = load_invoice("INV-003")  # Goes to vp_finance → pending
    result = engine.process(inv)
    assert result.status == "pending_approval"

    # Resume with approval
    result = engine.resume("INV-003", approved=True)
    assert result.status == "posted"
    assert result.approval.approved is True

    # Verify entries were saved
    assert engine.store.has_journal_entries("INV-003")

def test_hitl_resume_reject(engine):
    """Test the pending → reject flow."""
    inv = load_invoice("INV-003")
    engine.process(inv)

    result = engine.resume("INV-003", approved=False)
    assert result.status == "rejected"
    assert result.approval.approved is False
    assert not engine.store.has_journal_entries("INV-003")

def test_idempotency_guard(engine):
    """Cannot process an already-posted invoice."""
    inv = load_invoice("INV-005")
    # Simulate auto-approve by using a small invoice, or manually post
    # For testing: directly save some journal entries
    from src.models import JournalEntry, JournalLine
    from uuid import uuid4
    from datetime import date
    entry = JournalEntry(
        id=str(uuid4()), invoice_id="INV-005", date=date.today(),
        description="test", entry_type="initial",
        lines=[JournalLine(account_code="5050", account_name="test",
                           debit=Decimal("1"), memo="test"),
               JournalLine(account_code="2000", account_name="test",
                           credit=Decimal("1"), memo="test")]
    )
    engine.store.save_journal_entries([entry])

    result = engine.process(inv)
    assert result.status == "error"
    assert "already posted" in result.errors[0].lower()
```

---

## 4. README (`README.md`)

### Required Sections

```markdown
# Varick — Accounts Payable Agent

## Quick Start

### Prerequisites
- Python 3.13+
- uv (recommended) or pip

### Setup
$ git clone <repo>
$ cd varick
$ uv sync

### Usage
$ uv run gl process INV-001 --dry-run    # Preview classification + journal entries
$ uv run gl eval                          # Run accuracy evaluation
$ uv run gl shadow                        # Process unlabeled invoices
$ uv run gl resume INV-003 --approve      # Approve pending invoice
$ uv run gl feedback correct INV-002 1 --gl 5040  # Record correction
$ uv run gl feedback analyze              # Show error patterns
$ uv run gl feedback rerun                # Before/after accuracy

## Architecture

[Pipeline diagram: Invoice → PO Matcher → GL Classifier → Recognition → Approval → Poster]

### Module Structure
[Describe src/, eval/, data/, tests/]

### Key Design Decisions
1. Deterministic rules, not LLM — SOP is fully specified
2. Pydantic v2 models + SQLite — no ORM overhead
3. State-file HITL — testable, scriptable
4. Eval-as-infrastructure — 6 labeled invoices as test suite

## GL Classification Rules
[Summary of the 7 priority-ordered rules with examples]

## Tradeoffs
[Table from DESIGN.md §12]

## Known Limitations
[From DESIGN.md §13]
```

---

## 5. Edge Case Handling

### Items to address in this phase:

1. **Unclassifiable line items:** Engine returns `status="flagged"` with error messages listing which lines couldn't be classified. Already handled in Phase 2 engine, but ensure CLI displays this clearly.

2. **Rounding edge cases:** For non-clean amortization divisions ($25,000 / 12), the last-month-remainder strategy from Phase 2 handles this. Verify in e2e tests that amortization entries sum exactly to the original amount.

3. **Missing invoice ID:** `load_invoice()` raises `ValueError`. CLI should catch this and print a helpful error message.

4. **Resume of non-existent pending:** `store.load_pending()` raises `ValueError`. CLI should catch and say "No pending approval found for {id}".

5. **Empty corrections:** `analyze` with no corrections should print "No corrections recorded yet."

---

## 6. Acceptance Criteria

Phase 4 is done when:

- [ ] All CLI commands work: `process`, `eval`, `shadow`, `resume`, `feedback correct/analyze/rerun`
- [ ] `uv run gl process INV-001 --dry-run` shows correct output
- [ ] `uv run gl process INV-003` pauses → `uv run gl resume INV-003 --approve` posts
- [ ] `uv run gl eval` shows 100% accuracy across all dimensions
- [ ] `uv run gl shadow` shows proposals for 10 unlabeled invoices
- [ ] `uv run gl feedback` cycle works end-to-end
- [ ] All e2e tests pass (`tests/test_e2e.py`)
- [ ] Idempotency: can't double-post
- [ ] README is complete with setup, usage, architecture, tradeoffs
- [ ] All existing unit tests from Phase 1-3 still pass

---

## 7. Final Validation Checklist

Run these commands in sequence. All must succeed:

```bash
# 1. Run all tests
uv run pytest tests/ -v

# 2. Eval — expect 100% accuracy
uv run gl eval

# 3. Dry-run each labeled invoice
uv run gl process INV-001 --dry-run
uv run gl process INV-002 --dry-run
uv run gl process INV-003 --dry-run
uv run gl process INV-004 --dry-run
uv run gl process INV-005 --dry-run
uv run gl process INV-006 --dry-run

# 4. HITL flow
uv run gl process INV-003           # Should pause for approval
uv run gl resume INV-003 --approve  # Should post

# 5. Shadow mode
uv run gl shadow

# 6. Feedback cycle
uv run gl feedback correct INV-002 1 --gl 5030 --reason "test correction"
uv run gl feedback analyze
uv run gl feedback rerun
```
