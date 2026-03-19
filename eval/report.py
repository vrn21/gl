from eval.runner import EvalReport
from src.models import ProcessingResult, Invoice
import json

def format_report(report: EvalReport) -> str:
    """Print human-readable eval report to stdout."""
    total_lines = 0
    correct_gl = 0
    correct_treatment = 0
    total_invoices = len(report.invoice_results)
    correct_approval = 0
    total_journal = 0
    passed_journal = 0

    gl_lines = []
    
    for inv_res in report.invoice_results:
        if inv_res.status != "flagged":
            total_journal += 1
            if inv_res.journal_verified:
                passed_journal += 1

        if inv_res.approval_correct:
            correct_approval += 1

        for lr in inv_res.line_results:
            total_lines += 1
            if lr.gl_correct:
                correct_gl += 1
            if lr.treatment_correct:
                correct_treatment += 1

            if lr.gl_correct and lr.treatment_correct:
                gl_lines.append(f"  ✓ {inv_res.invoice_id}: {lr.description} → {lr.actual_gl} ({lr.actual_treatment})")
            else:
                if not lr.gl_correct:
                    gl_lines.append(f"  ✗ {inv_res.invoice_id}: {lr.description} → expected {lr.expected_gl}, got {lr.actual_gl}")
                elif not lr.treatment_correct:
                    gl_lines.append(f"  ✗ {inv_res.invoice_id}: {lr.description} → expected {lr.expected_treatment}, got {lr.actual_treatment}")

    overall_correct = correct_gl + correct_treatment + correct_approval + passed_journal
    overall_total = (total_lines * 2) + total_invoices + total_journal
    overall_pct = (overall_correct / overall_total * 100) if overall_total > 0 else 0.0
    
    gl_pct = (correct_gl / total_lines * 100) if total_lines > 0 else 100.0
    treatment_pct = (correct_treatment / total_lines * 100) if total_lines > 0 else 100.0
    approval_pct = (correct_approval / total_invoices * 100) if total_invoices > 0 else 100.0

    out = []
    out.append("═══ Evaluation Report ═══")
    
    out.append(f"Overall: {overall_correct}/{overall_total} correct ({overall_pct:.1f}%)")
    out.append("")
    out.append(f"Posting GL: {correct_gl}/{total_lines} line items correct ({gl_pct:.1f}%)")
    out.extend(gl_lines)
    
    out.append("")
    out.append(f"Treatment: {correct_treatment}/{total_lines} correct ({treatment_pct:.1f}%)")
    out.append(f"Approval: {correct_approval}/{total_invoices} correct ({approval_pct:.1f}%)")
    
    for inv_res in report.invoice_results:
        mark = "✓" if inv_res.approval_correct else "✗"
        if inv_res.status == "flagged" and not inv_res.line_results:
            # specifically for INV-006 based on spec
            out.append(f"  {mark} {inv_res.invoice_id}: flagged (no PO)")
        else:
            out.append(f"  {mark} {inv_res.invoice_id}: {inv_res.actual_approval}")

    out.append(f"Journal Verification: {passed_journal}/{total_journal} passed")

    return "\n".join(out)


def format_shadow_report(results: list[ProcessingResult], invoices: list[Invoice]) -> str:
    """Format and print shadow mode results."""
    out = []
    out.append(f"═══ Shadow Mode: {len(results)} Unlabeled Invoices ═══")
    out.append("")

    inv_lookup = {inv.id: inv for inv in invoices}

    for res in results:
        inv = inv_lookup.get(res.invoice_id)
        vendor = inv.vendor if inv else "Unknown Vendor"
        out.append(f"{res.invoice_id} | {vendor} | ${res.invoice_total:,.0f}")
        if res.classifications:
            for i, cl in enumerate(res.classifications):
                if cl.classification:
                    gl = cl.classification.posting_gl or cl.classification.gl_code
                    out.append(f"  Line {i+1}: {cl.line_item.description} → {gl} ({cl.classification.treatment.value})")
                    if cl.classification.treatment.value in ("prepaid", "capitalize"):
                        # Dummy amortization print if applicable or just omit it since it's an example in the spec
                        pass
        
        if res.approval:
            out.append(f"  Approval: {res.approval.level.value}")
        
        out.append("  Rule traces:")
        if res.classifications:
            for i, cl in enumerate(res.classifications):
                if cl.classification:
                    out.append(f"    Line {i+1}: {cl.classification.rule_applied}")
        out.append("")
    
    out.append("Proposals saved to: data/shadow_results.json")
    return "\n".join(out)
