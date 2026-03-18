import json
import pytest
from datetime import date
from decimal import Decimal
from pydantic import ValidationError

from src.models import (
    ApprovalDecision, ApprovalLevel, Classification, ClassifiedLine,
    Invoice, JournalEntry, JournalLine, LineItem, POMatchResult,
    ProcessingResult, PurchaseOrder, ServicePeriod, Treatment
)
from src.fixtures import load_invoices, load_purchase_orders
from src.store import Store

def test_service_period_validation():
    # Valid
    ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    ServicePeriod(start=date(2026, 1, 1), end=date(2026, 1, 1))

    # Invalid: start > end
    with pytest.raises(ValidationError) as exc:
        ServicePeriod(start=date(2026, 1, 2), end=date(2026, 1, 1))
    assert "start (2026-01-02) must be <= end (2026-01-01)" in str(exc.value)

def test_line_item_amount_validation():
    # Valid
    LineItem(description="Test", amount=Decimal("10.00"))

    # Invalid: amount = 0
    with pytest.raises(ValidationError):
        LineItem(description="Test", amount=Decimal("0.00"))

    # Invalid: amount < 0
    with pytest.raises(ValidationError):
        LineItem(description="Test", amount=Decimal("-10.00"))

def test_invoice_total_validation():
    # Valid
    Invoice(
        id="INV-TEST",
        vendor="Test Vendor",
        po_number=None,
        date=date(2026, 1, 1),
        department="Engineering",
        line_items=[
            LineItem(description="Item 1", amount=Decimal("100.00")),
            LineItem(description="Item 2", amount=Decimal("50.50")),
        ],
        total=Decimal("150.50")
    )

    # Invalid: mismatch
    with pytest.raises(ValidationError) as exc:
        Invoice(
            id="INV-TEST",
            vendor="Test Vendor",
            po_number=None,
            date=date(2026, 1, 1),
            department="Engineering",
            line_items=[
                LineItem(description="Item 1", amount=Decimal("100.00")),
                LineItem(description="Item 2", amount=Decimal("50.00")),
            ],
            total=Decimal("150.50")
        )
    assert "Invoice total (150.50) != sum of line items (150.00)" in str(exc.value)

def test_journal_entry_verify():
    entry = JournalEntry(
        id="je-1",
        invoice_id="inv-1",
        date=date(2026, 1, 1),
        description="Test Entry",
        entry_type="initial",
        lines=[
            JournalLine(account_code="5000", account_name="Office", debit=Decimal("100.00"), memo=""),
            JournalLine(account_code="2000", account_name="AP", credit=Decimal("100.00"), memo=""),
        ]
    )
    assert entry.verify() is True

    unbalanced = JournalEntry(
        id="je-2",
        invoice_id="inv-1",
        date=date(2026, 1, 1),
        description="Test Entry",
        entry_type="initial",
        lines=[
            JournalLine(account_code="5000", account_name="Office", debit=Decimal("100.00"), memo=""),
            JournalLine(account_code="2000", account_name="AP", credit=Decimal("90.00"), memo=""),
        ]
    )
    assert unbalanced.verify() is False

def test_fixture_loading():
    invoices = load_invoices()
    assert len(invoices) == 16
    assert isinstance(invoices[0], Invoice)
    # Check that they validate properly (already done during loading, but we ensure count is right)

    pos = load_purchase_orders()
    assert len(pos) == 15  # All except INV-006

def test_store_journal_roundtrip(tmp_path):
    # tmp_path is a pytest fixture that provides a temporary directory unique to the test invocation
    db_path = tmp_path / "test.db"
    store = Store(db_path)

    assert not store.has_journal_entries("inv-rt")

    entry = JournalEntry(
        id="je-rt-1",
        invoice_id="inv-rt",
        date=date(2026, 1, 1),
        description="Test Entry",
        entry_type="initial",
        lines=[
            JournalLine(account_code="5000", account_name="Office", debit=Decimal("100.00"), memo="debit line"),
            JournalLine(account_code="2000", account_name="AP", credit=Decimal("100.00"), memo="credit line"),
        ]
    )

    store.save_journal_entries([entry])
    assert store.has_journal_entries("inv-rt")

    # In actual use, we only load full entries when we need them. To test data integrity,
    # let's write a small raw query to verify what was saved matches.
    cursor = store.conn.execute("SELECT * FROM journal_entries WHERE invoice_id = ?", ("inv-rt",))
    rows = cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["id"] == "je-rt-1"
    assert row["entry_type"] == "initial"
    
    # Verify lines structure
    lines_data = json.loads(row["lines_json"])
    assert len(lines_data) == 2
    assert lines_data[0]["account_code"] == "5000"
    assert lines_data[0]["debit"] == "100.00"

def test_store_pending_roundtrip(tmp_path):
    db_path = tmp_path / "test.db"
    store = Store(db_path)

    assert not store.has_pending("inv-pending")

    po = load_purchase_orders().get("PO-2026-044")
    result = ProcessingResult(
        invoice_id="inv-pending",
        invoice_total=Decimal("24000.00"),
        status="pending_approval",
        po_result=POMatchResult(status="matched", matched=True, po=po, variance_pct=Decimal("0.00")),
        approval=ApprovalDecision(level=ApprovalLevel.VP_FINANCE, reason="Requires VP Finance approval due to amount"),
        journal_entries=[],
        classifications=[],
        errors=[],
        warnings=[]
    )

    store.save_pending(result)
    assert store.has_pending("inv-pending")

    loaded_result = store.load_pending("inv-pending")
    
    # Verify equality via model dump parity
    assert loaded_result.model_dump() == result.model_dump()
    assert isinstance(loaded_result.invoice_total, Decimal)
    assert loaded_result.invoice_id == "inv-pending"

    # Test delete
    store.delete_pending("inv-pending")
    assert not store.has_pending("inv-pending")
