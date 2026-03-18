# Phase 3 — Eval, Shadow & Feedback

> **Depends on:** Phase 1 (models, fixtures, store) + Phase 2 (pipeline, engine)
> **Produces:** Evaluation harness, shadow mode processing, feedback loop with before/after accuracy.
> **Estimated effort:** ~4 hours

---

## Goal

Build the systems that measure the agent's accuracy, process unlabeled invoices for human review, and support iterative improvement via a feedback loop. After this phase, `gl eval` shows accuracy, `gl shadow` produces proposals, and the feedback correction → re-eval cycle works.

---

## 1. Directory Structure

```
eval/
├── __init__.py
├── expected.py              # Ground truth for 6 labeled invoices
├── runner.py                # Run pipeline + compare to expected
├── report.py                # Accuracy metrics + formatted output
└── feedback.py              # Correction collector + analysis + rerun
```

> **`eval/` is a peer of `src/`, not inside it.** Eval code should NEVER be imported by production code.

---

## 2. Ground Truth (`eval/expected.py`)

### Structure

```python
EXPECTED: dict[str, dict] = {
    "INV-001": {
        "lines": [
            {"description": "Annual Platform License", "gl": "1310", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",   # > $10K
    },
    "INV-002": {
        "lines": [
            {"description": "Patent filing", "gl": "5030", "treatment": "expense"},
            {"description": "Regulatory compliance", "gl": "5040", "treatment": "expense"},
            {"description": "Contract review", "gl": "5030", "treatment": "expense"},
        ],
        "approval_level": "dept_manager",  # $1K–$10K
    },
    "INV-003": {
        "lines": [
            {"description": "MacBook Pro 14-inch (3x)", "gl": "5110", "treatment": "expense"},
            {"description": "Dell PowerEdge R760", "gl": "1500", "treatment": "capitalize"},
            {"description": "AWS Reserved Instances (annual)", "gl": "1300", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",    # Any 1500 → VP Finance
    },
    "INV-004": {
        "lines": [
            {"description": "Operational efficiency assessment", "gl": "2110", "treatment": "accrual"},
            {"description": "Travel expenses", "gl": "2100", "treatment": "accrual"},
        ],
        "approval_level": "dept_manager",  # $1K–$10K, Operations dept
    },
    "INV-005": {
        "lines": [
            {"description": "Q1 digital ad campaign management", "gl": "5050", "treatment": "expense"},
            {"description": "Branded company t-shirts (500 units)", "gl": "5000", "treatment": "expense"},
            {"description": "Conference booth rental", "gl": "5050", "treatment": "expense"},
            {"description": "Conference attendee gift bags (branded)", "gl": "5000", "treatment": "expense"},
        ],
        "approval_level": "vp_finance",    # > $10K ($23,500)
    },
    "INV-006": {
        "status": "flagged",               # No PO → stops at Step 1
        "lines": [],                       # No classifications
        "approval_level": None,            # Never reaches approval
    },
}
```

### Expected GL Semantics

The `gl` field in expected data is the **final posting GL**, not the classifier GL:

| Invoice | Expected GL | Where it comes from |
|---------|-------------|-------------------|
| INV-001 | 1310 | Classifier (prepaid, gl_code IS the posting account) |
| INV-004 | 2110/2100 | Recognition engine (posting_gl, set after accrual detection) |
| INV-003 line 2 | 1500 | Classifier (capitalize, gl_code IS the posting account) |

The eval comparison formula: `actual_gl = classification.posting_gl or classification.gl_code`

---

## 3. Eval Runner (`eval/runner.py`)

### Interface

```python
def run_eval(engine: Engine, invoices: list[Invoice]) -> EvalReport:
    """Process all labeled invoices and compare results to ground truth."""
```

### Logic

```python
from src.models import Invoice, ProcessingResult
from eval.expected import EXPECTED

@dataclass
class LineResult:
    invoice_id: str
    line_index: int
    description: str
    expected_gl: str
    actual_gl: str | None
    expected_treatment: str
    actual_treatment: str | None
    gl_correct: bool
    treatment_correct: bool

@dataclass
class InvoiceResult:
    invoice_id: str
    line_results: list[LineResult]
    expected_approval: str | None
    actual_approval: str | None
    approval_correct: bool
    journal_verified: bool
    status: str                    # "flagged", "posted", "error", etc.

@dataclass
class EvalReport:
    invoice_results: list[InvoiceResult]
    # Aggregate metrics calculated from invoice_results

def run_eval(engine, invoices):
    results = []
    for inv in invoices:
        if inv.id not in EXPECTED:
            continue  # Skip unlabeled invoices

        expected = EXPECTED[inv.id]
        processing_result = engine.process(inv, dry_run=True)

        # Handle flagged invoices (INV-006)
        if expected.get("status") == "flagged":
            results.append(InvoiceResult(
                invoice_id=inv.id,
                line_results=[],
                expected_approval=None,
                actual_approval=None,
                approval_correct=processing_result.status == "flagged",
                journal_verified=True,  # No entries to verify
                status=processing_result.status,
            ))
            continue

        # Compare line-by-line
        line_results = []
        for i, (expected_line, actual_cl) in enumerate(
            zip(expected["lines"], processing_result.classifications)
        ):
            actual_gl = None
            actual_treatment = None
            if actual_cl.classification:
                actual_gl = actual_cl.classification.posting_gl or actual_cl.classification.gl_code
                actual_treatment = actual_cl.classification.treatment.value

            line_results.append(LineResult(
                invoice_id=inv.id,
                line_index=i,
                description=expected_line["description"],
                expected_gl=expected_line["gl"],
                actual_gl=actual_gl,
                expected_treatment=expected_line["treatment"],
                actual_treatment=actual_treatment,
                gl_correct=actual_gl == expected_line["gl"],
                treatment_correct=actual_treatment == expected_line["treatment"],
            ))

        # Compare approval
        actual_approval = None
        if processing_result.approval:
            actual_approval = processing_result.approval.level.value
        expected_approval = expected.get("approval_level")

        results.append(InvoiceResult(
            invoice_id=inv.id,
            line_results=line_results,
            expected_approval=expected_approval,
            actual_approval=actual_approval,
            approval_correct=actual_approval == expected_approval,
            journal_verified=len(processing_result.journal_entries) == 0
                or poster.verify_journal_entries(
                    processing_result.journal_entries,
                    inv.total
                ),
            status=processing_result.status,
        ))

    return EvalReport(invoice_results=results)
```

### Key Detail: dry_run=True

The eval runner uses `dry_run=True` so it doesn't actually post entries or check idempotency. This ensures the eval can be run repeatedly.

---

## 4. Eval Report (`eval/report.py`)

### Metrics

Four independent dimensions:

| Metric | Formula | Denominator |
|--------|---------|-------------|
| **Posting GL accuracy** | Correct GLs / total classified lines | 13 (INV-006 has 0 lines) |
| **Treatment accuracy** | Correct treatments / total classified lines | 13 |
| **Approval accuracy** | Correct approvals / total invoices | 6 (including INV-006 flagged) |
| **Journal verification** | Entries that pass verification / total invoices with entries | 5 (INV-006 has no entries) |

### Output Format

```python
def format_report(report: EvalReport) -> str:
    """Print human-readable eval report to stdout."""
```

Target output:

```
═══ Evaluation Report ═══
Overall: 13/13 correct (100.0%)

Posting GL: 13/13 line items correct (100.0%)
  ✓ INV-001: Annual Platform License → 1310 (prepaid)
  ✓ INV-002: Patent filing → 5030 (expense)
  ✓ INV-002: Regulatory compliance → 5040 (expense)
  ✓ INV-002: Contract review → 5030 (expense)
  ...

Treatment: 13/13 correct (100.0%)
Approval: 6/6 correct (100.0%)
  ✓ INV-001: vp_finance
  ✓ INV-006: flagged (no PO)
  ...
Journal Verification: 5/5 passed
```

For incorrect results, show with `✗`:

```
  ✗ INV-002: Regulatory compliance → expected 5040, got 5030
```

---

## 5. Shadow Mode

### Processing

Shadow mode processes the 10 unlabeled invoices (UL-1 through UL-10) through the engine with `shadow=True`.

```python
def run_shadow(engine: Engine, invoices: list[Invoice]) -> list[ProcessingResult]:
    """Process unlabeled invoices in shadow mode. Returns list of proposals."""
    unlabeled = [inv for inv in invoices if inv.id.startswith("UL-")]
    results = []
    for inv in unlabeled:
        result = engine.process(inv, shadow=True)
        results.append(result)
    return results
```

### Output Format

```
═══ Shadow Mode: 10 Unlabeled Invoices ═══

UL-1 | DataSync Pro | $24,000
  Line 1: Annual Platform License → 1310 (Prepaid Software)
    Amortization: $1,500/mo × 12 months to 5010
  Line 2: Premium Support & Implementation → 5040 (Consulting)
  Approval: VP Finance (> $10K)
  Rule traces:
    Line 1: "Software annual prepayment → 1310"
    Line 2: "Consulting/advisory → 5040"

UL-3 | TalentBridge Partners | $25,000
  Line 1: Placement fee → 5040 (Consulting)
  Approval: VP Finance (> $10K)
  ...

... (8 more)

Proposals saved to: data/shadow_results.json
```

Shows the **rule trace** for each classification — which rule matched and what it matched on. No confidence scoring (the pipeline is deterministic).

### Shadow Results Persistence

Save shadow results to `data/shadow_results.json` as a list of serialized `ProcessingResult` objects (using `.model_dump_json()`). This file is overwritten on each shadow run.

---

## 6. Feedback Loop (`eval/feedback.py`)

### 6.1 Correction Storage

Corrections are stored in the `corrections` SQLite table (already created in Phase 1). The feedback module wraps the store methods.

```python
def save_correction(store: Store, invoice_id: str, line_index: int,
                    corrected_gl: str, corrected_treatment: str | None = None,
                    reason: str | None = None):
    """Save a human correction. Look up original values by re-running the pipeline."""
    # 1. Load the invoice from fixtures
    # 2. Run the pipeline in dry_run to get the current classification
    # 3. Extract the original gl and treatment from the classification
    # 4. Save to store
```

### 6.2 Error Pattern Analysis

```python
def analyze_corrections(store: Store) -> str:
    """Group corrections by original_gl → corrected_gl and show patterns."""
```

This is a simple SQL query:

```sql
SELECT original_gl, corrected_gl, COUNT(*) as count,
       GROUP_CONCAT(reason, '; ') as reasons
FROM corrections
GROUP BY original_gl, corrected_gl
ORDER BY count DESC
```

Output format:

```
═══ Error Pattern Analysis ═══
5030 → 5040: 2 corrections
  - "advisory services misclassified as legal"
  - "regulatory compliance is consulting, not legal"

5050 → 5000: 1 correction
  - "branded merch should be physical goods"
```

### 6.3 Before/After Accuracy Rerun

```python
def rerun_with_comparison(engine: Engine, invoices: list[Invoice], store: Store) -> str:
    """Re-run eval and compare to baseline."""
```

Logic:

1. Run the standard eval (current rules) → "current" report
2. Load all corrections from the store
3. Apply corrections as overrides to the eval comparison:
   - For each correction, if the current classification still differs from the corrected value, count it as "would be fixed"
4. Print "before" and "after" accuracy

Output:

```
═══ Before/After Accuracy ═══
                    Before    After
Posting GL:         12/13     13/13  (+1)
Treatment:          12/13     13/13  (+1)
Approval:            6/6       6/6
Overall:            92.3%    100.0%  (+7.7%)

Corrections applied:
  INV-002 line 1: 5030 → 5040 (advisory misclassified as legal)
```

> **Important:** The "after" accuracy uses corrections as overrides on the eval comparison, NOT by modifying the classifier rules at runtime. The feedback loop demonstrates that *if* the rules were fixed, accuracy would improve. The human developer then manually fixes the rules  in `classifier.py` and re-runs `gl eval` to verify.

---

## 7. Integration Points

### What the eval system imports from Phase 1/2

```python
from src.models import Invoice, ProcessingResult, ClassifiedLine
from src.engine import Engine
from src.fixtures import load_invoices, load_purchase_orders
from src.store import Store
from src.pipeline.poster import verify_journal_entries
```

### How the CLI will call these (Phase 4 will wire this)

```python
# gl eval
engine = Engine(store=Store(), po_store=load_purchase_orders())
invoices = load_invoices()
report = run_eval(engine, invoices)
print(format_report(report))

# gl shadow
results = run_shadow(engine, invoices)
print(format_shadow_report(results))

# gl feedback correct INV-002 1 --gl 5040
save_correction(store, "INV-002", 1, "5040")

# gl feedback analyze
print(analyze_corrections(store))

# gl feedback rerun
print(rerun_with_comparison(engine, invoices, store))
```

---

## 8. Tests

### `eval/` tests (or `tests/test_eval.py`)

1. **Ground truth completeness:** All 6 labeled invoice IDs exist in `EXPECTED`
2. **Eval runner with correct pipeline:** Run eval → all metrics should be 100% (if Phase 2 is correct)
3. **Eval runner with simulated wrong classification:** Mock a misclassification → accuracy drops
4. **Feedback round-trip:** Save correction → list corrections → correction appears
5. **Analysis:** Save 2 corrections with same `original_gl → corrected_gl` → analysis groups them

---

## 9. Acceptance Criteria

Phase 3 is done when:

- [ ] `eval/expected.py` has ground truth for all 6 labeled invoices
- [ ] `run_eval()` produces an `EvalReport` with correct metrics
- [ ] `format_report()` prints human-readable output with ✓/✗ per line
- [ ] `run_shadow()` processes all 10 unlabeled invoices and shows proposals
- [ ] Shadow report shows rule traces for each classification
- [ ] `save_correction()` persists corrections to SQLite
- [ ] `analyze_corrections()` shows grouped error patterns
- [ ] `rerun_with_comparison()` shows before/after accuracy delta
- [ ] Tests pass

---

## 10. What NOT to Build in Phase 3

- No CLI wiring (Phase 4)
- No HITL resume command (Phase 4)
- No production posting (Phase 4)
- No auto-modification of classifier rules — feedback is advisory, human fixes the code
