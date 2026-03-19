import pytest
from unittest.mock import patch
from src.store import Store
from src.engine import Engine
from src.fixtures import load_invoices, load_purchase_orders
from eval.expected import EXPECTED
from eval.runner import run_eval, run_shadow
from eval.feedback import save_correction, analyze_corrections, rerun_with_comparison

@pytest.fixture
def store(tmp_path):
    # Using an in-memory or temp sqlite database
    db_path = tmp_path / "test.db"
    return Store(db_path=db_path)

@pytest.fixture
def engine(store):
    po_store = load_purchase_orders()
    return Engine(store=store, po_store=po_store)

@pytest.fixture
def invoices():
    return load_invoices()

def test_ground_truth_completeness():
    """All 6 labeled invoice IDs exist in EXPECTED"""
    expected_ids = {f"INV-00{i}" for i in range(1, 7)}
    assert set(EXPECTED.keys()) == expected_ids

def test_eval_runner_accuracy(engine, invoices):
    """Run eval -> all metrics should be 100%"""
    report = run_eval(engine, invoices)
    
    # 6 invoices total in EXPECTED
    assert len(report.invoice_results) == 6
    
    for inv_res in report.invoice_results:
        assert inv_res.approval_correct is True
        if inv_res.status != "flagged":
            assert inv_res.journal_verified is True
            
        for lr in inv_res.line_results:
            assert lr.gl_correct is True
            assert lr.treatment_correct is True

def test_eval_runner_mocked_error(engine, invoices):
    """Mock a misclassification -> accuracy drops"""
    # Let's mock EXPECTED dict to expect something impossible
    original_expected = EXPECTED["INV-001"]["lines"][0]["gl"]
    EXPECTED["INV-001"]["lines"][0]["gl"] = "9999"
    
    report = run_eval(engine, invoices)
    
    # Now INV-001 line 0 should fail gl_correct
    inv001_res = next(r for r in report.invoice_results if r.invoice_id == "INV-001")
    assert inv001_res.line_results[0].gl_correct is False
    
    # Restore
    EXPECTED["INV-001"]["lines"][0]["gl"] = original_expected

def test_feedback_round_trip(store):
    """Save correction -> list corrections -> correction appears"""
    # Just call store directly or save_correction helper
    save_correction(store, "INV-002", 0, "5099", "expense", "felt like it")
    
    corrections = store.list_corrections()
    assert len(corrections) == 1
    assert corrections[0]["invoice_id"] == "INV-002"
    assert corrections[0]["corrected_gl"] == "5099"
    assert corrections[0]["reason"] == "felt like it"

def test_analysis_grouping(store):
    """Save 2 corrections with same original_gl -> corrected_gl -> analysis groups them"""
    save_correction(store, "INV-002", 0, "5099", "expense", "issue 1")
    save_correction(store, "INV-002", 1, "5099", "expense", "issue 2") # Wait, line index 1 is original gl 5040 so it's a different grouping.
    
    # Needs to be same original_gl. 
    # INV-002 line 0 has original gl 5030. Line 2 also has original gl 5030!
    save_correction(store, "INV-002", 2, "5099", "expense", "issue 3")
    
    output = analyze_corrections(store)
    
    # Should say "5030 → 5099: 2 corrections"
    assert "5030 → 5099: 2 corrections" in output
    assert "issue 1" in output
    assert "issue 3" in output
