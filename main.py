import click
import json
from pathlib import Path
from src.agent import process_invoice, ap_agent, AgentDeps
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
    result = process_invoice(inv, store, dry_run=dry_run)
    _print_result(result)
    
    if result.status == "pending_approval":
        # Serialize the full result to the pending_approvals table
        store.save_pending(inv.id, result.model_dump_json())
        click.echo("")
        click.echo(f"⏸ Invoice {inv.id} requires {result.approval_level} approval.")
        click.echo(f"  Reason: {result.approval_reason}")
        click.echo(f"  Run: gl resume {invoice_id} --approve")
        click.echo(f"       gl resume {invoice_id} --reject")
    elif result.status == "posted":
        entries = result.journal_entries or []
        click.echo(f"✅ Invoice {inv.id} posted. {len(entries)} entries saved.")

# ─── Eval Command ───

@cli.command()
def eval():
    """Run evaluation against all 6 labeled invoices."""
    from eval.runner import run_eval
    from eval.report import format_report
    store = Store()
    report = run_eval(store)
    click.echo(format_report(report))

# ─── Shadow Command ───

@cli.command()
def shadow():
    """Process 10 unlabeled invoices in shadow mode for human review."""
    from eval.runner import run_shadow
    from eval.report import format_shadow_report
    store = Store()
    invoices = load_invoices(unlabeled_only=True)
    
    results = []
    for invoice in invoices:
        result = process_invoice(invoice, store, shadow=True)
        results.append(result)
        
    click.echo(format_shadow_report(results, invoices))

# ─── Resume Command ───

@cli.command()
@click.argument("invoice_id")
@click.option("--approve", "decision", flag_value="approve", help="Approve the pending invoice")
@click.option("--reject", "decision", flag_value="reject", help="Reject the pending invoice")
def resume(invoice_id: str, decision: str):
    """Resume a pending invoice approval."""
    if decision is None:
        click.echo("Error: must specify --approve or --reject", err=True)
        raise SystemExit(1)
        
    approve = (decision == "approve")
    store = Store()
    pending_json = store.get_pending(invoice_id)
    if not pending_json:
        click.echo(f"No pending approval found for {invoice_id}")
        return
    
    pending = ProcessingResult.model_validate_json(pending_json)
    
    if approve:
        # Re-invoke the agent with the approval decision pre-loaded
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
    click.echo(rerun_with_comparison(store))

# ─── Result Display Helper ───

def _print_result(result: ProcessingResult):
    """Print a ProcessingResult to stdout in human-readable format."""
    click.echo(f"\n{'═' * 50}")
    click.echo(f"Invoice: {result.invoice_id}  |  Status: {result.status}")
    click.echo(f"{'═' * 50}")

    click.echo(f"PO Match: {result.po_match}")

    for cl in result.classifications:
        click.echo(f"  Line {cl.line_index}: {cl.description}")
        click.echo(f"    → {cl.gl_code} ({cl.treatment})  [{cl.rule_applied}]")

    if result.approval_level:
        click.echo(f"Approval: {result.approval_level} — {result.approval_reason}")

    if result.journal_entries:
        click.echo(f"\nJournal Entries ({len(result.journal_entries)}):")
        for entry in result.journal_entries:
            # Assuming journal entry models have been formatted as dicts from the tool
            desc = entry.get("description", "")
            date_str = entry.get("date", "")
            click.echo(f"  {date_str} — {desc}")
            for line in entry.get("lines", []):
                debit = float(line.get("debit", 0))
                credit = float(line.get("credit", 0))
                acct = line.get("account_code", "")
                name = line.get("account_name", "")
                if debit > 0:
                    click.echo(f"    Dr  {acct} {name:40s} {debit:>12}")
                if credit > 0:
                    click.echo(f"    Cr  {acct} {name:40s} {' '*12}{credit:>12}")

    for err in result.errors:
        click.echo(f"  ⚠ {err}", err=True)
    for warn in result.warnings:
        click.echo(f"  ⓘ {warn}")

if __name__ == "__main__":
    cli()
