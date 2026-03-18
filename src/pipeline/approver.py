from decimal import Decimal
from src.models import Invoice, ClassifiedLine, ApprovalDecision, ApprovalLevel

def route_approval(invoice: Invoice, classifications: list[ClassifiedLine]) -> ApprovalDecision:
    # 1. Fixed Asset check
    if any(cl.classification and cl.classification.gl_code == "1500" for cl in classifications):
        return ApprovalDecision(
            level=ApprovalLevel.VP_FINANCE,
            reason="Fixed asset (1500) requires VP Finance approval"
        )
        
    # 2. Department overrides
    if invoice.department == "Marketing" and invoice.total <= Decimal("2500"):
        return ApprovalDecision(
            level=ApprovalLevel.AUTO,
            reason="Marketing auto-approve (≤$2.5K)"
        )
        
    if invoice.department == "Engineering" and invoice.total <= Decimal("5000"):
        if all(cl.classification and cl.classification.gl_code in ("5010", "5020") for cl in classifications):
            return ApprovalDecision(
                level=ApprovalLevel.AUTO,
                reason="Engineering auto-approve (Cloud/Software ≤$5K)"
            )
            
    # 3. Base thresholds
    if invoice.total <= Decimal("1000"):
        return ApprovalDecision(
            level=ApprovalLevel.AUTO,
            reason="Base threshold (≤$1K)"
        )
    elif Decimal("1000") < invoice.total <= Decimal("10000"):
        return ApprovalDecision(
            level=ApprovalLevel.DEPT_MANAGER,
            reason="Base threshold ($1K - $10K)"
        )
    elif invoice.total > Decimal("10000"):
        return ApprovalDecision(
            level=ApprovalLevel.VP_FINANCE,
            reason="Base threshold (>$10K)"
        )
        
    # 4. Default: DENIED (fail-closed)
    return ApprovalDecision(
        level=ApprovalLevel.DENIED,
        reason="Does not match any approval threshold"
    )
