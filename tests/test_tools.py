import json
import pytest
from decimal import Decimal
from src.tools import calculate_amortization, check_accrual

def test_amortization_12_months():
    result = calculate_amortization("24000.00", "1310", "5010", "2026-01-01", "2026-12-31")
    schedule = json.loads(result)
    assert schedule["treatment"] == "prepaid"
    assert len(schedule["monthly_entries"]) == 12
    assert all(Decimal(e["amount"]) == Decimal("2000.00") for e in schedule["monthly_entries"])

def test_accrual_detection():
    result = check_accrual("2026-01-15", "2025-12-31", "7500.00", True)
    data = json.loads(result)
    assert data["treatment"] == "accrual"
    assert data["accrual_account"] == "2110"
