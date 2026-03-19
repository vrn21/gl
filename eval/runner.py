from dataclasses import dataclass
from src.models import Invoice, ProcessingResult
from src.agent import process_invoice
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
    status: str

@dataclass
class EvalReport:
    invoice_results: list[InvoiceResult]

def run_eval(store) -> EvalReport:
    """Process all labeled invoices and compare results to ground truth."""
    from src.fixtures import load_invoices
    invoices = [inv for inv in load_invoices() if inv.id.startswith("INV-")]
    
    results = []
    for inv in invoices:
        if inv.id not in EXPECTED:
            continue  # Skip unlabeled invoices

        expected = EXPECTED[inv.id]
        processing_result = process_invoice(inv, store, dry_run=True)

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
            actual_gl = actual_cl.gl_code
            actual_treatment = actual_cl.treatment

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
        actual_approval = processing_result.approval_level
        expected_approval = expected.get("approval_level")

        # Journal verification check
        journal_verified = processing_result.verification_passed if processing_result.verification_passed is not None else True

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

def run_shadow(store, invoices: list[Invoice]) -> list[ProcessingResult]:
    """Process unlabeled invoices in shadow mode. Returns list of proposals."""
    unlabeled = [inv for inv in invoices if inv.id.startswith("UL-")]
    results = []
    for inv in unlabeled:
        result = process_invoice(inv, store, shadow=True)
        results.append(result)
    return results
