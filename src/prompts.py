from pathlib import Path
from pydantic_ai import RunContext
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.agent import AgentDeps

SOP_PATH = Path(__file__).parent.parent / "data" / "sop.md"

def build_system_prompt(ctx: RunContext['AgentDeps']) -> str:
    """
    Dynamically builds the system prompt. This is called ONCE per agent invocation.
    Loads the SOP from data/sop.md and appends few-shot corrections from the feedback DB.
    """
    base_prompt = SOP_PATH.read_text()
    corrections = ctx.deps.store.list_corrections()

    if corrections:
        correction_block = "\n## Past Corrections (Learn From These)\n\n"
        correction_block += "The following are corrections made by human reviewers. "
        correction_block += "You MUST apply these lessons:\n\n"
        for c in corrections:
            correction_block += (
                f"- Invoice {c['invoice_id']}, Line {c['line_index']}: "
                f"Was classified as {c['original_gl']}, should be {c['corrected_gl']}. "
                f"Reason: {c['reason']}\n"
            )
        base_prompt += correction_block

    return base_prompt
