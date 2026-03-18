import json
from pathlib import Path
from decimal import Decimal
from src.models import Invoice, PurchaseOrder

DATA_DIR = Path(__file__).parent.parent / "data"

def _decimal_hook(obj: dict) -> dict:
    """Convert string amounts to Decimal during JSON parsing."""
    # This is handled by Pydantic's Decimal coercion, so we can pass raw dicts.
    return obj

def load_invoices() -> list[Invoice]:
    """Load all invoices from data/invoices.json."""
    with open(DATA_DIR / "invoices.json") as f:
        data = json.load(f)
    return [Invoice.model_validate(inv) for inv in data]

def load_invoice(invoice_id: str) -> Invoice:
    """Load a single invoice by ID. Raises ValueError if not found."""
    for inv in load_invoices():
        if inv.id == invoice_id:
            return inv
    raise ValueError(f"Invoice {invoice_id} not found in fixtures")

def load_purchase_orders() -> dict[str, PurchaseOrder]:
    """Load POs as a lookup dict keyed by PO number."""
    with open(DATA_DIR / "purchase_orders.json") as f:
        data = json.load(f)
    return {po["number"]: PurchaseOrder.model_validate(po) for po in data}
