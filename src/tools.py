import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from pydantic_ai import RunContext

from src.agent import ap_agent, AgentDeps

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
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
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
        return "auto_approve: Invoice total <= $1,000."
    elif total <= Decimal("10000"):
        return f"dept_manager: Invoice total ${total} requires department manager approval."
    elif total > Decimal("10000"):
        return f"vp_finance: Invoice total ${total} exceeds $10,000. Requires VP Finance approval."
    
    return "denied: No matching approval rule. Fail closed."


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
        if e.get("credit") == "2000"
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
    
    import uuid
    from datetime import date
    from src.models import JournalEntry, JournalLine

    je_models = []
    for entry in entries["entries"]:
        je = JournalEntry(
            id=str(uuid.uuid4()),
            invoice_id=ctx.deps.invoice.id,
            date=date.today(),
            description=entry.get("description", f"Journal Entry for {ctx.deps.invoice.id}"),
            entry_type=entry.get("type", "initial"),
            lines=[
                JournalLine(
                    account_code=entry["debit"],
                    account_name="Debit Account",
                    debit=Decimal(entry["amount"]),
                    credit=Decimal("0"),
                    memo=entry.get("description", "")
                ),
                JournalLine(
                    account_code=entry["credit"],
                    account_name="Credit Account",
                    debit=Decimal("0"),
                    credit=Decimal(entry["amount"]),
                    memo=entry.get("description", "")
                )
            ]
        )
        je_models.append(je)
        
    ctx.deps.store.save_journal_entries(je_models)
    return f"SUCCESS: {len(je_models)} journal entries saved for {ctx.deps.invoice.id}."
