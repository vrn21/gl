from dataclasses import dataclass
from src.models import Invoice, ProcessingResult
from src.engine import Engine
from eval.expected import EXPECTED
from src.pipeline.poster import verify_journal_entries

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
    status: str

@dataclass
class EvalReport:
    invoice_results: list[InvoiceResult]

def run_eval(engine: Engine, invoices: list[Invoice]) -> EvalReport:
    """Process all labeled invoices and compare results to ground truth."""
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

        # Journal verification check
        # len(processing_result.journal_entries) == 0 might happen for flagged or errors
        journal_verified = True
        if processing_result.journal_entries:
            journal_verified = verify_journal_entries(
                processing_result.journal_entries,
                inv.total
            )

        results.append(InvoiceResult(
            invoice_id=inv.id,
            line_results=line_results,
            expected_approval=expected_approval,
            actual_approval=actual_approval,
            approval_correct=actual_approval == expected_approval,
            journal_verified=journal_verified,
            status=processing_result.status,
        ))

    return EvalReport(invoice_results=results)

def run_shadow(engine: Engine, invoices: list[Invoice]) -> list[ProcessingResult]:
    """Process unlabeled invoices in shadow mode. Returns list of proposals."""
    unlabeled = [inv for inv in invoices if inv.id.startswith("UL-")]
    results = []
    for inv in unlabeled:
        result = engine.process(inv, shadow=True)
        results.append(result)
    return results
