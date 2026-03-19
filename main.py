import click
from pathlib import Path
from src.engine import Engine
from src.store import Store
from src.fixtures import load_invoices, load_invoice, load_purchase_orders
from src.models import ProcessingResult

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
    try:
        inv = load_invoice(invoice_id)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    
    result = engine.process(inv, dry_run=dry_run)
    _print_result(result)
    
    if result.status == "pending_approval":
        click.echo("")
        click.echo(f"⏸ Invoice saved for approval. Run:")
        click.echo(f"  gl resume {invoice_id} --approve")
        click.echo(f"  gl resume {invoice_id} --reject")

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
    from eval.report import format_shadow_report
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoices = load_invoices()
    results = run_shadow(engine, invoices)
    click.echo(format_shadow_report(results, invoices))

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
    try:
        result = engine.resume(invoice_id, approved=(decision == "approve"))
        _print_result(result)
        if result.status == "posted":
            click.echo(f"✓ Journal entries posted ({len(result.journal_entries)} entries)")
    except ValueError as e:
        click.echo(f"No pending approval found for {invoice_id}", err=True)
        raise SystemExit(1)

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
    analysis = analyze_corrections(store)
    if not analysis or analysis.strip() == "═══ Error Pattern Analysis ═══":
        click.echo("No corrections recorded yet.")
    else:
        click.echo(analysis)

@feedback.command()
def rerun():
    """Re-run eval and show before/after accuracy comparison."""
    from eval.feedback import rerun_with_comparison
    store = Store()
    po_store = load_purchase_orders()
    engine = Engine(store=store, po_store=po_store)
    invoices = load_invoices()
    click.echo(rerun_with_comparison(engine, invoices, store))

# ─── Result Display Helper ───

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


if __name__ == "__main__":
    cli()
