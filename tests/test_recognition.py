from decimal import Decimal
import pytest
from datetime import date

from src.models import Invoice, LineItem, ServicePeriod, Classification, ClassifiedLine, Treatment
from src.pipeline.recognition import (
    months_between, is_annual, monthly_periods, generate_entries, resolve_service_period
)

def make_classified_line(desc: str, amount: Decimal, gl_code: str, treatment: Treatment) -> ClassifiedLine:
    return ClassifiedLine(
        line_item=LineItem(description=desc, amount=amount),
        classification=Classification(gl_code=gl_code, treatment=treatment, rule_applied="test")
    )

def test_months_between():
    assert months_between(date(2026, 1, 1), date(2026, 12, 31)) == 12
    assert months_between(date(2026, 1, 1), date(2026, 1, 31)) == 1
    assert months_between(date(2026, 2, 1), date(2027, 1, 31)) == 12

def test_is_annual():
    assert is_annual(ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))) is True
    assert is_annual(ServicePeriod(start=date(2026, 1, 1), end=date(2026, 1, 31))) is False
    assert is_annual(None) is False

def test_monthly_periods():
    period = ServicePeriod(start=date(2026, 1, 15), end=date(2026, 3, 14))
    months = monthly_periods(period)
    assert months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]

def test_resolve_service_period():
    inv_period = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    line_period = ServicePeriod(start=date(2026, 2, 1), end=date(2026, 2, 28))
    
    item = LineItem(description="test", amount=Decimal("100"), service_period=line_period)
    inv = Invoice(id="INV-1", vendor="V", date=date(2026, 3, 1), department="D", line_items=[item], total=Decimal("100"), service_period=inv_period, po_number=None)
    
    # Line level wins
    assert resolve_service_period(item, inv) == line_period
    
    item.service_period = None
    # Falls back to invoice level
    assert resolve_service_period(item, inv) == inv_period

def test_generate_expense_entries():
    cline = make_classified_line("Simple expense", Decimal("120"), "5000", Treatment.EXPENSE)
    inv = Invoice(id="INV-1", vendor="V", date=date(2026, 1, 1), department="D", line_items=[cline.line_item], total=Decimal("120"), po_number=None)
    
    entries = generate_entries(cline, inv)
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry.entry_type == "initial"
    assert entry.date == date(2026, 1, 1)
    
    assert len(entry.lines) == 2
    assert entry.lines[0].account_code == "5000"
    assert entry.lines[0].debit == Decimal("120")
    assert entry.lines[1].account_code == "2000"
    assert entry.lines[1].credit == Decimal("120")

def test_generate_prepaid_entries_clean():
    cline = make_classified_line("Annual Software", Decimal("24000"), "1310", Treatment.PREPAID)
    cline.line_item.service_period = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    inv = Invoice(id="INV-1", vendor="V", date=date(2025, 12, 15), department="D", line_items=[cline.line_item], total=Decimal("24000"), po_number=None)
    
    entries = generate_entries(cline, inv)
    assert len(entries) == 13 # 1 initial + 12 amortization
    
    # Initial
    assert entries[0].entry_type == "initial"
    assert entries[0].lines[0].account_code == "1310"
    assert entries[0].lines[0].debit == Decimal("24000")
    assert entries[0].lines[1].account_code == "2000"
    assert entries[0].lines[1].credit == Decimal("24000")
    
    # Amortization check
    total_amortized = Decimal("0")
    for entry in entries[1:]:
        assert entry.entry_type == "amortization"
        assert entry.lines[0].account_code == "5010" # Expense code for 1310
        assert entry.lines[1].account_code == "1310"
        total_amortized += entry.lines[0].debit
        
    assert total_amortized == Decimal("24000")
    assert entries[1].lines[0].debit == Decimal("2000")

def test_generate_prepaid_entries_remainder():
    cline = make_classified_line("Annual Insurance", Decimal("25000"), "1320", Treatment.PREPAID)
    cline.line_item.service_period = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    inv = Invoice(id="INV-1", vendor="V", date=date(2025, 12, 15), department="D", line_items=[cline.line_item], total=Decimal("25000"), po_number=None)
    
    entries = generate_entries(cline, inv)
    assert len(entries) == 13
    
    # Check rounding
    # 25000 / 12 = 2083.333... -> 2083.33
    amort_1_11 = [e.lines[0].debit for e in entries[1:12]]
    assert all(d == Decimal("2083.33") for d in amort_1_11)
    
    # Last month gets remainder
    # 2083.33 * 11 = 22916.63. 25000 - 22916.63 = 2083.37
    last_amort = entries[12].lines[0].debit
    assert last_amort == Decimal("2083.37")
    
    assert sum([e.lines[0].debit for e in entries[1:]]) == Decimal("25000")

def test_generate_accrual_entries():
    cline = make_classified_line("Consulting services", Decimal("5000"), "5040", Treatment.EXPENSE)
    # Service period ended Dec 31, 2025. Invoice date Jan 15, 2026. This is an accrual.
    cline.line_item.service_period = ServicePeriod(start=date(2025, 12, 1), end=date(2025, 12, 31))
    inv = Invoice(id="INV-1", vendor="V", date=date(2026, 1, 15), department="D", line_items=[cline.line_item], total=Decimal("5000"), po_number=None)
    
    entries = generate_entries(cline, inv)
    
    # Treatment mutates to ACCRUAL
    assert cline.classification.treatment == Treatment.ACCRUAL
    assert cline.classification.posting_gl == "2110" # Accrued Prof Services
    
    assert len(entries) == 2
    
    accrual = entries[0]
    assert accrual.entry_type == "accrual"
    assert accrual.date == date(2025, 12, 31)
    assert accrual.lines[0].account_code == "5040"
    assert accrual.lines[0].debit == Decimal("5000")
    assert accrual.lines[1].account_code == "2110"
    assert accrual.lines[1].credit == Decimal("5000")
    
    reversal = entries[1]
    assert reversal.entry_type == "reversal"
    assert reversal.date == date(2026, 1, 15)
    assert reversal.lines[0].account_code == "2110"
    assert reversal.lines[0].debit == Decimal("5000")
    assert reversal.lines[1].account_code == "2000" # AP
    assert reversal.lines[1].credit == Decimal("5000")

def test_generate_accrual_entries_non_prof_service():
    cline = make_classified_line("Coworking", Decimal("1000"), "5070", Treatment.EXPENSE)
    cline.line_item.service_period = ServicePeriod(start=date(2025, 12, 1), end=date(2025, 12, 31))
    inv = Invoice(id="INV-1", vendor="V", date=date(2026, 1, 15), department="D", line_items=[cline.line_item], total=Decimal("1000"), po_number=None)

    
    entries = generate_entries(cline, inv)
    assert cline.classification.posting_gl == "2100" # Accrued Gen Expenses
    assert entries[0].lines[1].account_code == "2100"
