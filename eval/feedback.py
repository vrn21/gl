import sqlite3
from src.store import Store
from src.models import Invoice
from src.fixtures import load_invoices, load_purchase_orders
from src.agent import process_invoice
from eval.runner import run_eval

def save_correction(store: Store, invoice_id: str, line_index: int,
                    corrected_gl: str, corrected_treatment: str | None = None,
                    reason: str | None = None):
    """Save a human correction. Look up original values by having the agent classify."""
    invoices = load_invoices()
    inv_map = {inv.id: inv for inv in invoices}
    if invoice_id not in inv_map:
        raise ValueError(f"Invoice {invoice_id} not found in fixtures.")
    
    invoice = inv_map[invoice_id]
    if line_index < 0 or line_index >= len(invoice.line_items):
        raise IndexError(f"Line index {line_index} out of bounds for {invoice_id}")
    
    # We must see the agent's current classification
    result = process_invoice(invoice, store, dry_run=True)
    
    if line_index < len(result.classifications):
        cl = result.classifications[line_index]
        original_gl = cl.gl_code
        original_treatment = cl.treatment
    else:
        original_gl = "None"
        original_treatment = "None"
        
    store.save_correction(
        invoice_id=invoice_id,
        line_index=line_index,
        original_gl=original_gl,
        corrected_gl=corrected_gl,
        original_treatment=original_treatment,
        corrected_treatment=corrected_treatment,
        reason=reason
    )

def analyze_corrections(store: Store) -> str:
    """Group corrections by original_gl → corrected_gl and show patterns."""
    query = """
    SELECT original_gl, corrected_gl, COUNT(*) as count,
           GROUP_CONCAT(reason, '; ') as reasons
    FROM corrections
    GROUP BY original_gl, corrected_gl
    ORDER BY count DESC
    """
    cursor = store.conn.execute(query)
    rows = cursor.fetchall()
    
    out = ["═══ Error Pattern Analysis ═══"]
    for row in rows:
        out.append(f"{row['original_gl']} → {row['corrected_gl']}: {row['count']} correction{'s' if row['count'] != 1 else ''}")
        reasons = []
        if row['reasons']:
            raw_reasons = row['reasons'].split('; ')
            for r in raw_reasons:
                if r and r not in reasons:
                    reasons.append(r)
        
        for r in reasons:
            out.append(f"  - {repr(r)}")
        out.append("")
        
    return "\n".join(out).strip()

def rerun_with_comparison(store: Store) -> str:
    """Re-run eval and compare to baseline."""
    # Since we can't easily turn off corrections without modifying store or agent state, 
    # the comparison logic directly uses the corrections table as intended in the spec.
    # Actually, in this agentic design, the `run_eval` naturally includes corrections via few-shot.
    # To show "before" accuracy, we could temporarily clear corrections, but the assignment just asks
    # to show the improvement which the manual or test script can capture. 
    # For now, let's just run an eval and show the report. The spec hints that running `gl feedback rerun`
    # shows Before/After accuracy comparison. It might be easiest to compute the "Before" by evaluating without
    # corrections, or just computing it dynamically.
    
    # To do a real before/after, we need to bypass corrections for "before".
    # We can inject a mock store that returns no corrections for the 'before' run.
    class NoCorrectionStore(Store):
        def list_corrections(self):
            return []
            
    before_store = NoCorrectionStore(store.db_path)
    before_report = run_eval(before_store)
    
    after_report = run_eval(store)
    
    # Calculate metrics
    def calc_metrics(report):
        lines_correct_gl = 0
        lines_correct_treatment = 0
        lines_total = 0
        invs_correct_approval = 0
        invs_total = len(report.invoice_results)
        
        for inv_res in report.invoice_results:
            if inv_res.approval_correct:
                invs_correct_approval += 1
            for lr in inv_res.line_results:
                lines_total += 1
                if lr.gl_correct:
                    lines_correct_gl += 1
                if lr.treatment_correct:
                    lines_correct_treatment += 1
                    
        def pct(n, d): return (n / d * 100) if d > 0 else 100.0
        return {
            "gl": (lines_correct_gl, lines_total, pct(lines_correct_gl, lines_total)),
            "treatment": (lines_correct_treatment, lines_total, pct(lines_correct_treatment, lines_total)),
            "approval": (invs_correct_approval, invs_total, pct(invs_correct_approval, invs_total)),
        }
        
    b = calc_metrics(before_report)
    a = calc_metrics(after_report)
    
    # Overall sum for demo purposes as in the spec
    b_overall_correct = b["gl"][0] + b["treatment"][0] + b["approval"][0]
    b_overall_total = b["gl"][1] + b["treatment"][1] + b["approval"][1]
    b_overall_pct = (b_overall_correct / b_overall_total * 100) if b_overall_total > 0 else 100.0
    
    a_overall_correct = a["gl"][0] + a["treatment"][0] + a["approval"][0]
    a_overall_total = a["gl"][1] + a["treatment"][1] + a["approval"][1]
    a_overall_pct = (a_overall_correct / a_overall_total * 100) if a_overall_total > 0 else 100.0

    overall_delta = a_overall_pct - b_overall_pct

    def format_delta(d, is_pct=False):
        if d > 0:
            return f"(+{d:.1f}{'%' if is_pct else ''})"
        elif d < 0:
            return f"({d:.1f}{'%' if is_pct else ''})"
        return ""
        
    out = []
    out.append("═══ Before/After Accuracy ═══")
    out.append(f"{'':<20}{'Before':<10}{'After':<10}")
    bg_0, bg_1 = b["gl"][0], b["gl"][1]
    ag_0, ag_1 = a["gl"][0], a["gl"][1]
    bt_0, bt_1 = b["treatment"][0], b["treatment"][1]
    at_0, at_1 = a["treatment"][0], a["treatment"][1]
    ba_0, ba_1 = b["approval"][0], b["approval"][1]
    aa_0, aa_1 = a["approval"][0], a["approval"][1]

    out.append(f"{'Posting GL:':<20}{f'{bg_0}/{bg_1}':<10}{f'{ag_0}/{ag_1}':<10} {format_delta(ag_0 - bg_0)}")
    out.append(f"{'Treatment:':<20}{f'{bt_0}/{bt_1}':<10}{f'{at_0}/{at_1}':<10} {format_delta(at_0 - bt_0)}")
    out.append(f"{'Approval:':<20}{f'{ba_0}/{ba_1}':<10}{f'{aa_0}/{aa_1}':<10}")
    out.append(f"{'Overall:':<20}{f'{b_overall_pct:.1f}%':<10}{f'{a_overall_pct:.1f}%':<10} {format_delta(overall_delta, True)}")

    return "\n".join(out)
