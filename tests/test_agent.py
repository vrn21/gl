import pytest
from pydantic_ai.models.test import TestModel
from src.agent import ap_agent, process_invoice
from src.store import Store
from src.fixtures import load_invoice
from src.models import ProcessingResult, ClassifiedLine
import json

def test_agent_returns_valid_result():
    """Test that the agent produces a valid ProcessingResult structure."""
    store = Store(":memory:")
    invoice = load_invoice("INV-001")
    
    # Create a mock valid result string to inject into TestModel
    mock_result = ProcessingResult(
        invoice_id="INV-001",
        status="posted",
        po_match="matched",
        classifications=[
            ClassifiedLine(
                line_index=0,
                description="Test Line",
                amount="1000",
                gl_code="5010",
                gl_account_name="Software",
                treatment="expense",
                rule_applied="Rule 3"
            )
        ],
        verification_passed=True,
    )
    
    test_model = TestModel(custom_output_args=mock_result, call_tools=[])

    with ap_agent.override(model=test_model):
        result = process_invoice(invoice, store, dry_run=True)
        assert isinstance(result, ProcessingResult)
        assert result.invoice_id == "INV-001"
