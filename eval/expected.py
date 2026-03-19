from typing import Any

EXPECTED: dict[str, dict[str, Any]] = {
    "INV-001": {
        "lines": [
            {"description": "Annual Platform License", "gl": "1310", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",   # > $10K
    },
    "INV-002": {
        "lines": [
            {"description": "Patent filing", "gl": "5030", "treatment": "expense"},
            {"description": "Regulatory compliance", "gl": "5040", "treatment": "expense"},
            {"description": "Contract review", "gl": "5030", "treatment": "expense"},
        ],
        "approval_level": "dept_manager",  # $1K–$10K
    },
    "INV-003": {
        "lines": [
            {"description": "MacBook Pro 14-inch (3x)", "gl": "5110", "treatment": "expense"},
            {"description": "Dell PowerEdge R760", "gl": "1500", "treatment": "capitalize"},
            {"description": "AWS Reserved Instances (annual)", "gl": "1300", "treatment": "prepaid"},
        ],
        "approval_level": "vp_finance",    # Any 1500 → VP Finance
    },
    "INV-004": {
        "lines": [
            {"description": "Operational efficiency assessment", "gl": "2110", "treatment": "accrual"},
            {"description": "Travel expenses", "gl": "2100", "treatment": "accrual"},
        ],
        "approval_level": "dept_manager",  # $1K–$10K, Operations dept
    },
    "INV-005": {
        "lines": [
            {"description": "Q1 digital ad campaign management", "gl": "5050", "treatment": "expense"},
            {"description": "Branded company t-shirts (500 units)", "gl": "5000", "treatment": "expense"},
            {"description": "Conference booth rental", "gl": "5050", "treatment": "expense"},
            {"description": "Conference attendee gift bags (branded)", "gl": "5000", "treatment": "expense"},
        ],
        "approval_level": "vp_finance",    # > $10K ($23,500)
    },
    "INV-006": {
        "status": "flagged",               # No PO → stops at Step 1
        "lines": [],                       # No classifications
        "approval_level": None,            # Never reaches approval
    },
}
