from decimal import Decimal
from src.models import Invoice, PurchaseOrder, POMatchResult

def match_po(invoice: Invoice, po_store: dict[str, PurchaseOrder]) -> POMatchResult:
    """
    Match invoice to PO by number. Validate amount ±10%.
    Returns POMatchResult with match status, PO reference, and variance.
    """
    if invoice.po_number is None:
        return POMatchResult(
            status="no_po",
            matched=False,
            reason="No PO number provided"
        )
        
    if invoice.po_number not in po_store:
        return POMatchResult(
            status="po_not_found",
            matched=False,
            reason=f"PO {invoice.po_number} not found"
        )
        
    po = po_store[invoice.po_number]
    variance = abs(invoice.total - po.amount) / po.amount
    
    if variance > Decimal("0.10"):
        return POMatchResult(
            status="amount_mismatch",
            matched=False,
            po=po,
            variance_pct=variance,
            reason=f"Amount variance ({variance:.2%}) exceeds 10% tolerance"
        )
        
    return POMatchResult(
        status="matched",
        matched=True,
        po=po,
        variance_pct=variance,
        reason=""
    )
