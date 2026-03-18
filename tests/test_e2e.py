import pytest
from decimal import Decimal
from datetime import date

from src.models import Invoice, LineItem, PurchaseOrder, ServicePeriod, ProcessingResult, ApprovalLevel
from src.engine import Engine

class MockStore:
    def __init__(self):
        self.posted = set()
        self.pending = {}
        self.flagged = []
        self.journals = []
        
    def is_posted(self, invoice_id):
        return invoice_id in self.posted
        
    def is_pending(self, invoice_id):
        return invoice_id in self.pending
        
    def save_flagged(self, result):
        self.flagged.append(result)
        
    def save_pending(self, result):
        self.pending[result.invoice_id] = result
        
    def save_journal(self, result):
        self.journals.append(result)
        self.posted.add(result.invoice_id)
        
    def get_pending(self, invoice_id):
        return self.pending.get(invoice_id)
        
    def delete_pending(self, invoice_id):
        if invoice_id in self.pending:
            del self.pending[invoice_id]

@pytest.fixture
def store():
    return MockStore()

@pytest.fixture
def po_store():
    return {
        "PO-001": PurchaseOrder(number="PO-001", vendor="V", amount=Decimal("24000"), department="Engineering"),
        "PO-002": PurchaseOrder(number="PO-002", vendor="V", amount=Decimal("100"), department="Marketing"),
    }

def make_invoice(id, po, dept, lines, date_=None) -> Invoice:
    total = sum(l.amount for l in lines)
    if not date_:
        date_ = date(2026, 1, 15)
    return Invoice(
        id=id, vendor="V", po_number=po, date=date_,
        department=dept, line_items=lines, total=total
    )

def test_engine_inv_001_prepaid(store, po_store):
    # INV-001: 1310 prepaid + 12 amortization entries
    # Amount 24000. Dept Engineering. PO PO-001
    lines = [
        LineItem(
            description="Annual Platform License",
            amount=Decimal("24000"),
            service_period=ServicePeriod(start=date(2026,1,1), end=date(2026,12,31))
        )
    ]
    inv = make_invoice("INV-001", "PO-001", "Engineering", lines)
    
    engine = Engine(store, po_store)
    res = engine.process(inv, dry_run=True)
    
    assert res.status == "pending_approval" # > 10000 -> VP Finance
    assert res.approval.level == ApprovalLevel.VP_FINANCE
    assert len(res.journal_entries) == 13 # 1 initial + 12 amort
    # 1st is initial
    assert res.journal_entries[0].entry_type == "initial"
    assert res.journal_entries[0].lines[0].account_code == "1310"

def test_engine_inv_006_flagged_no_po(store, po_store):
    # No PO provided -> flagged early
    lines = [LineItem(description="Office supplies", amount=Decimal("100"))]
    inv = make_invoice("INV-006", None, "Operations", lines)
    
    engine = Engine(store, po_store)
    res = engine.process(inv, dry_run=True)
    
    assert res.status == "flagged"
    assert res.po_result.status == "no_po"
    assert len(res.journal_entries) == 0

def test_resume_flow(store, po_store):
    lines = [LineItem(description="Office supplies", amount=Decimal("100"))]
    inv = make_invoice("INV-002", "PO-002", "Marketing", lines)
    engine = Engine(store, po_store)
    
    # Process normally (not dry_run) - will AUTO approve
    res = engine.process(inv)
    assert res.status == "posted"
    assert store.is_posted("INV-002")
    
    # What if we process a VP_FINANCE one?
    lines2 = [LineItem(description="MacBook Pro", amount=Decimal("24000"), unit_cost=Decimal("24000"))]
    inv2 = make_invoice("INV-003", "PO-001", "Engineering", lines2) # 24k matches PO-001
    
    res2 = engine.process(inv2)
    assert res2.status == "pending_approval"
    assert store.is_pending("INV-003")
    
    # Resume accept
    res3 = engine.resume("INV-003", True)
    assert res3.status == "posted"
    assert store.is_posted("INV-003")
    assert not store.is_pending("INV-003")
