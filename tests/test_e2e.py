import pytest
from decimal import Decimal
from src.engine import Engine
from src.store import Store
from src.fixtures import load_invoice, load_purchase_orders

@pytest.fixture
def engine(tmp_path):
    store = Store(db_path=tmp_path / "test.db")
    po_store = load_purchase_orders()
    return Engine(store=store, po_store=po_store)

def test_inv001_cloudware_prepaid(engine):
    """INV-001: $24K annual software license → 1310 prepaid + 12 amortization entries."""
    inv = load_invoice("INV-001")
    result = engine.process(inv, dry_run=True)

    assert result.status == "pending_approval"
    assert len(result.classifications) == 1

    cl = result.classifications[0].classification
    assert cl.gl_code == "1310"
    assert cl.treatment.value == "prepaid"

    # 1 initial + 12 amortization = 13 entries
    assert len(result.journal_entries) == 13
    assert result.journal_entries[0].entry_type == "initial"
    assert all(e.entry_type == "amortization" for e in result.journal_entries[1:])

    # Amortization sum = $24,000
    amort_total = sum(
        l.debit for e in result.journal_entries[1:] for l in e.lines
        if l.account_code == "5010"
    )
    assert amort_total == Decimal("24000.00")

    assert result.approval.level.value == "vp_finance"

def test_inv002_morrison_burke_legal(engine):
    """INV-002: 3 professional service lines → 5030/5040/5030."""
    inv = load_invoice("INV-002")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    assert gls == ["5030", "5040", "5030"]
    assert all(cl.classification.treatment.value == "expense"
               for cl in result.classifications)
    assert result.approval.level.value == "dept_manager"

def test_inv003_techdirect_mixed(engine):
    """INV-003: Mixed invoice — laptops (5110), server (1500), AWS prepaid (1300)."""
    inv = load_invoice("INV-003")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    treatments = [cl.classification.treatment.value for cl in result.classifications]
    assert gls == ["5110", "1500", "1300"]
    assert treatments == ["expense", "capitalize", "prepaid"]
    assert result.approval.level.value == "vp_finance"  # Any 1500 → VP Finance

def test_inv004_apex_accrual(engine):
    """INV-004: Dec 2025 service billed Jan 2026 → accrual entries."""
    inv = load_invoice("INV-004")
    result = engine.process(inv, dry_run=True)

    # Both lines should be accrual
    for cl in result.classifications:
        assert cl.classification.treatment.value == "accrual"

    # Check posting_gl (accrual accounts set by recognition)
    posting_gls = [cl.classification.posting_gl for cl in result.classifications]
    assert posting_gls == ["2110", "2100"]

    # Each accrual line produces 2 entries (accrual + reversal) = 4 total
    assert len(result.journal_entries) == 4
    types = [e.entry_type for e in result.journal_entries]
    assert types.count("accrual") == 2
    assert types.count("reversal") == 2

def test_inv005_brightspark_branded_merch(engine):
    """INV-005: Branded merch → 5000, marketing → 5050. Merch exception tested."""
    inv = load_invoice("INV-005")
    result = engine.process(inv, dry_run=True)

    gls = [cl.classification.gl_code for cl in result.classifications]
    assert gls == ["5050", "5000", "5050", "5000"]
    assert result.approval.level.value == "vp_finance"

def test_inv006_no_po_flagged(engine):
    """INV-006: No PO → flagged, no classifications, no approval."""
    inv = load_invoice("INV-006")
    result = engine.process(inv, dry_run=True)

    assert result.status == "flagged"
    assert len(result.classifications) == 0
    assert result.approval is None
    assert any("PO" in err for err in result.errors)

def test_hitl_resume_approve(engine):
    """Test the full pending → approve → posted flow."""
    inv = load_invoice("INV-003")  # Goes to vp_finance → pending
    result = engine.process(inv)
    assert result.status == "pending_approval"

    # Resume with approval
    result = engine.resume("INV-003", approved=True)
    assert result.status == "posted"
    assert result.approval.approved is True

    # Verify entries were saved
    assert engine.store.has_journal_entries("INV-003")

def test_hitl_resume_reject(engine):
    """Test the pending → reject flow."""
    inv = load_invoice("INV-003")
    engine.process(inv)

    result = engine.resume("INV-003", approved=False)
    assert result.status == "rejected"
    assert result.approval.approved is False
    assert not engine.store.has_journal_entries("INV-003")

def test_idempotency_guard(engine):
    """Cannot process an already-posted invoice."""
    inv = load_invoice("INV-005")
    
    # Simulate auto-approve by using a small invoice, or manually post
    # For testing: directly save some journal entries
    from src.models import JournalEntry, JournalLine
    from uuid import uuid4
    from datetime import date
    entry = JournalEntry(
        id=str(uuid4()), invoice_id="INV-005", date=date.today(),
        description="test", entry_type="initial",
        lines=[JournalLine(account_code="5050", account_name="test",
                           debit=Decimal("1"), memo="test"),
               JournalLine(account_code="2000", account_name="test",
                           credit=Decimal("1"), memo="test")]
    )
    engine.store.save_journal_entries([entry])

    result = engine.process(inv)
    assert result.status == "error"
    assert "already posted" in result.errors[0].lower()
