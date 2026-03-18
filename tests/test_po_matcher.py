from decimal import Decimal
import pytest
from datetime import date
from src.models import Invoice, LineItem, PurchaseOrder
from src.pipeline.po_matcher import match_po

def make_invoice(total_amount: Decimal, po_number: str | None = "PO-001") -> Invoice:
    return Invoice(
        id="INV-001",
        vendor="Test Vendor",
        po_number=po_number,
        date=date(2026, 1, 1),
        department="Engineering",
        line_items=[
            LineItem(description="Test item", amount=total_amount)
        ],
        total=total_amount
    )

def make_po(amount: Decimal, number: str = "PO-001") -> dict[str, PurchaseOrder]:
    return {
        number: PurchaseOrder(
            number=number,
            vendor="Test Vendor",
            amount=amount,
            department="Engineering"
        )
    }

def test_po_matched_exact():
    # 1. Invoice with matching PO (exact amount) → matched=True
    inv = make_invoice(Decimal("1000.00"))
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is True
    assert result.status == "matched"
    assert result.variance_pct == Decimal("0.0")

def test_po_no_po_number():
    # 2. Invoice with no po_number → status="no_po"
    inv = make_invoice(Decimal("1000.00"), po_number=None)
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is False
    assert result.status == "no_po"

def test_po_not_found():
    # 3. Invoice with PO number not in store → status="po_not_found"
    inv = make_invoice(Decimal("1000.00"), po_number="PO-999")
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is False
    assert result.status == "po_not_found"

def test_po_within_tolerance():
    # 4. Invoice 5% over PO amount → matched=True (within tolerance)
    inv = make_invoice(Decimal("1050.00"))
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is True
    assert result.status == "matched"
    assert result.variance_pct == Decimal("0.05")

def test_po_exceeds_tolerance():
    # 5. Invoice 15% over PO amount → status="amount_mismatch"
    inv = make_invoice(Decimal("1150.00"))
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is False
    assert result.status == "amount_mismatch"
    assert result.variance_pct == Decimal("0.15")

def test_po_boundary_exact():
    # 6. Invoice exactly 10% over → matched=True (boundary case)
    inv = make_invoice(Decimal("1100.00"))
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is True
    assert result.status == "matched"
    assert result.variance_pct == Decimal("0.10")

def test_po_boundary_exceeds():
    # 7. Invoice 10.01% over → matched=False
    inv = make_invoice(Decimal("1100.10"))
    po_store = make_po(Decimal("1000.00"))
    result = match_po(inv, po_store)
    assert result.matched is False
    assert result.status == "amount_mismatch"
    assert result.variance_pct > Decimal("0.10")
