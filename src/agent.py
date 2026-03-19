from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
import json
import os
from dotenv import load_dotenv

load_dotenv()

from src.models import Invoice, ProcessingResult
from src.store import Store
from src.fixtures import load_purchase_orders
from src.prompts import build_system_prompt


@dataclass
class AgentDeps:
    """Dependencies injected into every tool call."""
    invoice: Invoice
    po_store: dict           # PO number → PurchaseOrder
    store: Store             # SQLite persistence
    dry_run: bool = False
    shadow: bool = False


ap_agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=AgentDeps,
    output_type=ProcessingResult,
    instructions=build_system_prompt,  # Dynamic — includes few-shot corrections
    retries=2,
)

# Import tools so they register with ap_agent
import src.tools

def process_invoice(invoice: Invoice, store: Store, dry_run: bool = False, shadow: bool = False) -> ProcessingResult:
    """Process a single invoice through the AP Agent."""
    deps = AgentDeps(
        invoice=invoice,
        po_store=load_purchase_orders(),
        store=store,
        dry_run=dry_run,
        shadow=shadow,
    )
    
    prompt = (
        f"Process this invoice:\n"
        f"Invoice ID: {invoice.id}\n"
        f"Vendor: {invoice.vendor}\n"
        f"Department: {invoice.department}\n"
        f"Date: {invoice.date}\n"
        f"Total: ${invoice.total}\n"
        f"PO: {invoice.po_number or 'NONE'}\n\n"
        f"Follow the SOP exactly. Start with Step 1 (PO Matching)."
    )
    
    if dry_run:
        prompt += "\n\nThis is a DRY RUN. Do NOT call save_to_db. Show what would be posted."
    elif shadow:
        prompt += "\n\nThis is SHADOW MODE. Do NOT call save_to_db. Produce a proposal for human review."
    
    result = ap_agent.run_sync(prompt, deps=deps)
    return result.output
