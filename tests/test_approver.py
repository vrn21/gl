from decimal import Decimal
import pytest
from datetime import date

from src.models import Invoice, LineItem, ClassifiedLine, Classification, Treatment, ApprovalLevel
from src.pipeline.approver import route_approval

def make_invoice(total: Decimal, dept: str) -> Invoice:
    return Invoice(
        id="INV",
        vendor="V",
        po_number=None,
        date=date.today(),
        department=dept,
        line_items=[LineItem(description="desc", amount=total)],
        total=total
    )

def make_classifications(gl_codes: list[str]) -> list[ClassifiedLine]:
    return [
        ClassifiedLine(
            line_item=LineItem(description="desc", amount=Decimal("1")),
            classification=Classification(gl_code=code, treatment=Treatment.EXPENSE, rule_applied="test")
        ) for code in gl_codes
    ]

def test_invoice_500_any_dept():
    inv = make_invoice(Decimal("500"), "Operations")
    decision = route_approval(inv, make_classifications(["5000"]))
    assert decision.level == ApprovalLevel.AUTO

def test_invoice_5000_operations():
    inv = make_invoice(Decimal("5000"), "Operations")
    decision = route_approval(inv, make_classifications(["5000"]))
    assert decision.level == ApprovalLevel.DEPT_MANAGER

def test_invoice_15000_any_dept():
    inv = make_invoice(Decimal("15000"), "Operations")
    decision = route_approval(inv, make_classifications(["5000"]))
    assert decision.level == ApprovalLevel.VP_FINANCE

def test_fixed_asset():
    inv = make_invoice(Decimal("500"), "Engineering") # Amount under threshold
    decision = route_approval(inv, make_classifications(["1500", "5010"]))
    assert decision.level == ApprovalLevel.VP_FINANCE # Still VP_FINANCE

def test_marketing_override_success():
    inv = make_invoice(Decimal("2000"), "Marketing")
    decision = route_approval(inv, make_classifications(["5050"]))
    assert decision.level == ApprovalLevel.AUTO

def test_marketing_override_fail():
    inv = make_invoice(Decimal("8000"), "Marketing") # > 2500
    decision = route_approval(inv, make_classifications(["5050"]))
    assert decision.level == ApprovalLevel.DEPT_MANAGER

def test_engineering_override_success():
    inv = make_invoice(Decimal("4000"), "Engineering")
    decision = route_approval(inv, make_classifications(["5010", "5020"]))
    assert decision.level == ApprovalLevel.AUTO

def test_engineering_override_fail_gl_code():
    inv = make_invoice(Decimal("4000"), "Engineering")
    decision = route_approval(inv, make_classifications(["5010", "5110"])) # 5110 breaks it
    assert decision.level == ApprovalLevel.DEPT_MANAGER

def test_engineering_override_fail_prepaid():
    inv = make_invoice(Decimal("4000"), "Engineering")
    decision = route_approval(inv, make_classifications(["1310"])) # Prepaid software
    assert decision.level == ApprovalLevel.DEPT_MANAGER

def test_engineering_override_fail_amount():
    inv = make_invoice(Decimal("6000"), "Engineering")
    decision = route_approval(inv, make_classifications(["5010", "5020"]))
    assert decision.level == ApprovalLevel.DEPT_MANAGER
