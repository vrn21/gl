from decimal import Decimal
from src.models import JournalEntry

def verify_journal_entries(entries: list[JournalEntry], invoice_total: Decimal) -> bool:
    """
    Verify that journal entries are balanced and correctly equal the invoice total.
    Returns True if valid, False otherwise.
    """
    if not entries:
        # Cannot be empty
        return False

    # 1. Each entry must balance internally
    for entry in entries:
        if not entry.verify():
            return False
            
    # 2. Total AP (2000) credits across ALL entries must equal invoice total
    ap_credits = sum(
        l.credit for e in entries for l in e.lines
        if l.account_code == "2000"
    )
    
    return ap_credits == invoice_total
