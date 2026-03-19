import os
import pytest
from src.store import Store
from src.fixtures import load_invoice
from src.agent import process_invoice

@pytest.mark.skipif(os.getenv("OPENAI_API_KEY", "dummy") == "dummy", reason="No real API key")
def test_inv001_full_pipeline():
    result = process_invoice(load_invoice("INV-001"), Store(":memory:"), dry_run=True)
    assert result.status == "pending_approval"
    assert result.classifications[0].gl_code == "1310"
    assert result.classifications[0].treatment == "prepaid"
