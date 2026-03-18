from decimal import Decimal
from datetime import date
import pytest

from src.models import JournalEntry, JournalLine
from src.pipeline.poster import verify_journal_entries

def make_entry(lines: list[tuple[str, str, str, str]]) -> JournalEntry:
    # lines = [(code, name, debit, credit), ...]
    jlines = [
        JournalLine(account_code=code, account_name=name, debit=Decimal(d), credit=Decimal(c), memo="")
        for code, name, d, c in lines
    ]
    return JournalEntry(
        id="test",
        invoice_id="inv1",
        date=date.today(),
        description="test",
        lines=jlines,
        entry_type="test"
    )

def test_verify_success_simple_expense():
    # 1 entry: Debit expense, Credit AP
    entry = make_entry([
        ("5000", "Expense", "100.00", "0"),
        ("2000", "AP", "0", "100.00")
    ])
    assert verify_journal_entries([entry], Decimal("100.00")) is True

def test_verify_fail_unbalanced_entry():
    # Debits != Credits
    entry = make_entry([
        ("5000", "Expense", "100.00", "0"),
        ("2000", "AP", "0", "90.00")
    ])
    assert verify_journal_entries([entry], Decimal("100.00")) is False

def test_verify_fail_ap_mismatch():
    # AP credits != invoice total
    entry = make_entry([
        ("5000", "Expense", "90.00", "0"),
        ("2000", "AP", "0", "90.00")
    ])
    # Total is 100, but AP only has 90
    assert verify_journal_entries([entry], Decimal("100.00")) is False

def test_verify_success_prepaid_entries():
    # Initial + Amortizations
    initial = make_entry([
        ("1310", "Prepaid", "12000", "0"),
        ("2000", "AP", "0", "12000") # $12,000 matches invoice total
    ])
    # Say, 2 amortizations
    amort1 = make_entry([
        ("5010", "Expense", "6000", "0"),
        ("1310", "Prepaid", "0", "6000")
    ])
    amort2 = make_entry([
        ("5010", "Expense", "6000", "0"),
        ("1310", "Prepaid", "0", "6000")
    ])
    
    # Amortizations don't touch AP. Total AP credits is still 12000.
    assert verify_journal_entries([initial, amort1, amort2], Decimal("12000")) is True

def test_verify_success_accrual_entries():
    # Accrual: Debit Expense, Credit Accrual
    accrual = make_entry([
        ("5040", "Expense", "5000", "0"),
        ("2110", "Accrued", "0", "5000")
    ])
    # Reversal: Debit Accrual, Credit AP
    reversal = make_entry([
        ("2110", "Accrued", "5000", "0"),
        ("2000", "AP", "0", "5000") # $5,000 matches invoice total
    ])
    
    assert verify_journal_entries([accrual, reversal], Decimal("5000")) is True

def test_empty_entries():
    assert verify_journal_entries([], Decimal("100")) is False
