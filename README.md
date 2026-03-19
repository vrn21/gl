# gl Accounts Payable Agent

## Quick Start

### Prerequisites
- Python 3.13+
- uv (recommended) or pip

### Setup
```bash
git clone <repo>
cd varick
uv sync
```

### Usage
```bash
uv run gl process INV-001 --dry-run    # Preview classification + journal entries
uv run gl eval                          # Run accuracy evaluation
uv run gl shadow                        # Process unlabeled invoices
uv run gl resume INV-003 --approve      # Approve pending invoice
uv run gl feedback correct INV-002 1 --gl 5040  # Record correction
uv run gl feedback analyze              # Show error patterns
uv run gl feedback rerun                # Before/after accuracy
```

## Architecture

Pipeline diagram: Invoice → PO Matcher → GL Classifier → Recognition → Approval → Poster

### Module Structure
- `src/`: Core engine pipeline (matching, classifying, approval routing, journal posting, and SQLite state).
- `eval/`: Tooling for running evaluation reports, shadow proposals, and managing feedback loop metrics.
- `data/`: Invoices and purchase orders JSON fixtures used as ground truth and system inputs.
- `tests/`: End-to-end validation scenarios leveraging localized test databases.

### Key Design Decisions
1. Deterministic rules, not LLM — SOP is fully specified
2. Pydantic v2 models + SQLite — no ORM overhead
3. State-file HITL — testable, scriptable
4. Eval-as-infrastructure — 6 labeled invoices as test suite

## GL Classification Rules
1. Exception: Branded Merchandise ("branded", "swag", "merch") -> 5000 (Expense)
2. Marketing: ("marketing", "ads", "advertising", "campaign", "sponsorship") -> 5050 (Expense)
3. Hardware/Equipment: ("laptop", "server", "monitor", "hardware", "macbook", "ipad", "router", "switch", "workstation")
   - < $5,000 -> 5110 (Expense)
   - >= $5,000 -> 1500 (Capitalize)
4. Legal/Professional: ("legal", "counsel", "retainer", "audit", "tax", "consulting", "advisory", "professional")
   - "legal" / "counsel" / "retainer" -> 5030 (Expense)
   - "audit" / "tax" / "consulting" / "advisory" / "professional" -> 5040 (Expense)
5. Software/SaaS: ("software", "saas", "license", "subscription", "platform", "cloud", "aws", "gcp", "azure", "hosting")
   - Annual Service Period -> 1310 (Prepaid) [or 1300]
   - Other -> 5010 (Expense)
   - Cloud Infrastructure ("aws", "gcp", "azure", "hosting") > Monthly -> 1300 (Prepaid) / Monthly -> 5020 (Expense)
6. Facilities/Office: ("rent", "lease", "cleaning", "supplies", "office", "utilities", "electricity", "water", "internet", "building", "maintenance")
   - -> 5100 (Expense) / exceptions for Prepaid rent apply depending on period duration.
7. Fallback:
   - -> 5999 (Expense, Uncategorized)

## Tradeoffs
1. Hardcoded Rules vs. ML/AI: Rules are brittle for new domains but provide 100% determinism and traceability.
2. SQLite vs. Postgres: Simple setup, embedded, but limited concurrent write scalability.
3. No Auth/Roles: Current user identity is implicit; simplifies CLI UX but lacks enterprise audit controls.

## Known Limitations
1. Doesn't support automated document parsing (requires pre-structured JSON).
2. Limited to predefined vendor patterns and simple PO matching.
3. Hardcoded approval matrices are not dynamic.
4. Doesn't integrate live with an ERP (runs simulated outputs).
