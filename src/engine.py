import uuid
from src.models import Invoice, PurchaseOrder, ProcessingResult, ClassifiedLine, ApprovalLevel
from src.store import Store
from src.pipeline.po_matcher import match_po
from src.pipeline.classifier import classify_line_item
from src.pipeline.recognition import generate_entries
from src.pipeline.approver import route_approval
from src.pipeline.poster import verify_journal_entries

class Engine:
    def __init__(self, store: Store, po_store: dict[str, PurchaseOrder]):
        self.store = store
        self.po_store = po_store

    def process(self, invoice: Invoice, *, dry_run=False, shadow=False) -> ProcessingResult:
        result = ProcessingResult(
            invoice_id=invoice.id,
            invoice_total=invoice.total,
            status="processing"
        )
        
        # 1. Idempotency check 
        if not (dry_run or shadow):
            # Assumes store has methods to check if already posted or pending
            # If so, raise or return error. For simplicity, we assume store.get_result(id) exists if needed.
            if self.store.has_journal_entries(invoice.id):
                result.status = "error"
                result.errors.append(f"Invoice {invoice.id} already posted")
                return result
            if self.store.has_pending(invoice.id):
                result.status = "error" 
                result.errors.append(f"Invoice {invoice.id} is already pending approval")
                return result

        # Step 1: PO matching
        po_result = match_po(invoice, self.po_store)
        result.po_result = po_result
        if not po_result.matched:
            result.status = "flagged"
            if po_result.reason:
                result.errors.append(po_result.reason)
            if not (dry_run or shadow):
                self.store.save_pending(result)
            return result
            
        # Step 2: GL Classification
        for item in invoice.line_items:
            classification = classify_line_item(item, invoice)
            result.classifications.append(
                ClassifiedLine(line_item=item, classification=classification)
            )
            
        if any(cl.classification is None for cl in result.classifications):
            result.status = "flagged"
            result.warnings.append("One or more line items could not be classified")
            if not (dry_run or shadow):
                self.store.save_pending(result)
            return result
            
        # Step 3: Journal entries (Recognition)
        # Note Phase 2 specs "Shadow Mode Warning: When 0 < (invoice.date - period.end).days <= 7, engine adds a warning"
        for cl in result.classifications:
            entries = generate_entries(cl, invoice)
            
            # Set entry IDs and add to result
            for entry in entries:
                entry.id = str(uuid.uuid4())
                result.journal_entries.append(entry)
                
            # Shadow Mode Warning check
            # resolve_service_period logic
            period = cl.line_item.service_period or invoice.service_period
            if period:
                days_diff = (invoice.date - period.end).days
                if 0 < days_diff <= 7:
                    result.warnings.append(f"Late invoice warning: Service ended {days_diff} days before invoice date")

        # Step 4: Approval Router
        approval = route_approval(invoice, result.classifications)
        result.approval = approval
        
        # Step 5: Verify Journal Entries
        if not verify_journal_entries(result.journal_entries, invoice.total):
            result.status = "error"
            result.errors.append("Journal entry verification failed: Debits != Credits or AP != Total")
            if not (dry_run or shadow):
                # Hard fail
                pass
            return result

        # Mode-specific exits
        if dry_run or shadow:
            if approval.level == ApprovalLevel.DENIED:
                result.status = "flagged"
            elif approval.level != ApprovalLevel.AUTO:
                result.status = "pending_approval"
            else:
                result.status = "posted"
            return result
            
        # Normal Mode
        if approval.level == ApprovalLevel.DENIED:
            # Denied: save to pending (allow HITL override)
            result.status = "flagged"
            self.store.save_pending(result)
        elif approval.level != ApprovalLevel.AUTO:
            result.status = "pending_approval"
            self.store.save_pending(result)
        else:
            result.status = "posted"
            self.store.save_journal_entries(result.journal_entries)
            
        return result

    def resume(self, invoice_id: str, approved: bool) -> ProcessingResult:
        result = self.store.load_pending(invoice_id)
        
        if result.approval:
            result.approval.approved = approved
            
        if not approved:
            result.status = "rejected"
            self.store.delete_pending(invoice_id)
            return result
            
        if result.invoice_total is None:
            result.status = "error"
            result.errors.append("Verification failed: invoice total is missing.")
            return result

        if not verify_journal_entries(result.journal_entries, result.invoice_total):
            result.status = "error"
            result.errors.append("Verification failed during resume")
            return result
            
        result.status = "posted"
        self.store.delete_pending(invoice_id)
        self.store.save_journal_entries(result.journal_entries)
        return result
