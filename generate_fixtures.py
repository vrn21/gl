import json
from decimal import Decimal
import os

invoices = [
    {
        "id": "INV-001",
        "vendor": "Cloudware Solutions",
        "po_number": "PO-2026-044",
        "date": "2026-01-05",
        "department": "Engineering",
        "total": "24000.00",
        "line_items": [
            {
                "description": "Annual Platform License (Jan–Dec 2026)",
                "amount": "24000.00",
                "service_period": {"start": "2026-01-01", "end": "2026-12-31"}
            }
        ]
    },
    {
        "id": "INV-002",
        "vendor": "Morrison & Burke LLP",
        "po_number": "PO-2026-051",
        "date": "2026-01-20",
        "department": "Legal",
        "total": "9500.00",
        "line_items": [
            {"description": "Patent filing & prosecution", "amount": "4500.00"},
            {"description": "Regulatory compliance review & advisory", "amount": "3200.00"},
            {"description": "Contract review for marketing partnership", "amount": "1800.00"}
        ]
    },
    {
        "id": "INV-003",
        "vendor": "TechDirect Inc.",
        "po_number": "PO-2026-038",
        "date": "2026-02-01",
        "department": "Engineering",
        "total": "49900.00",
        "line_items": [
            {"description": "MacBook Pro 14\" (3x $1,800)", "amount": "5400.00", "unit_cost": "1800.00", "quantity": 3},
            {"description": "Dell PowerEdge R760 Server", "amount": "8500.00", "unit_cost": "8500.00", "quantity": 1},
            {"description": "AWS Reserved Instances Annual (Feb 26–Jan 27)", "amount": "36000.00", "service_period": {"start": "2026-02-01", "end": "2027-01-31"}}
        ]
    },
    {
        "id": "INV-004",
        "vendor": "Apex Strategy Group",
        "po_number": "PO-2025-189",
        "date": "2026-01-15",
        "department": "Operations",
        "total": "8700.00",
        "service_period": {"start": "2025-12-01", "end": "2025-12-31"},
        "line_items": [
            {"description": "Operational efficiency assessment – Dec 2025", "amount": "7500.00"},
            {"description": "Travel expenses – Dec 2025 on-site visits", "amount": "1200.00"}
        ]
    },
    {
        "id": "INV-005",
        "vendor": "BrightSpark Agency",
        "po_number": "PO-2026-062",
        "date": "2026-02-10",
        "department": "Marketing",
        "total": "23500.00",
        "line_items": [
            {"description": "Q1 digital ad campaign management", "amount": "15000.00"},
            {"description": "Branded company t-shirts (500 units)", "amount": "2000.00"},
            {"description": "Conference booth rental – Mar 2026", "amount": "5000.00"},
            {"description": "Conference attendee gift bags (branded)", "amount": "1500.00"}
        ]
    },
    {
        "id": "INV-006",
        "vendor": "QuickPrint Co.",
        "po_number": None,
        "date": "2026-02-20",
        "department": "Marketing",
        "total": "3800.00",
        "line_items": [
            {"description": "Rush printing – sales brochures (5,000 units)", "amount": "3800.00"}
        ]
    },
    {
        "id": "UL-1",
        "vendor": "DataSync Pro",
        "po_number": "PO-2026-070",
        "date": "2026-02-15",
        "department": "Engineering",
        "total": "24000.00",
        "line_items": [
            {"description": "Annual Platform License (Feb 26–Jan 27)", "amount": "18000.00", "service_period": {"start": "2026-02-01", "end": "2027-01-31"}},
            {"description": "Premium Support & Implementation Services", "amount": "6000.00"}
        ]
    },
    {
        "id": "UL-2",
        "vendor": "WeWork",
        "po_number": "PO-2026-071",
        "date": "2026-03-01",
        "department": "Operations",
        "total": "4500.00",
        "line_items": [
            {"description": "Monthly hot desk membership - 15 seats, Mar 2026", "amount": "4500.00"}
        ]
    },
    {
        "id": "UL-3",
        "vendor": "TalentBridge Partners",
        "po_number": "PO-2026-072",
        "date": "2026-02-28",
        "department": "Engineering",
        "total": "25000.00",
        "line_items": [
            {"description": "Placement fee – Senior Software Engineer", "amount": "25000.00"}
        ]
    },
    {
        "id": "UL-4",
        "vendor": "Global Tech Summit",
        "po_number": "PO-2026-073",
        "date": "2026-03-05",
        "department": "Engineering",
        "total": "6500.00",
        "line_items": [
            {"description": "Conference registration (3 attendees)", "amount": "3000.00"},
            {"description": "Flight + hotel – SF, 3 nights", "amount": "3500.00"}
        ]
    },
    {
        "id": "UL-5",
        "vendor": "RenewTech",
        "po_number": "PO-2026-074",
        "date": "2026-03-10",
        "department": "Operations",
        "total": "4500.00",
        "line_items": [
            {"description": "Refurbished Dell 27\" monitors (10x $450)", "amount": "4500.00", "unit_cost": "450.00", "quantity": 10}
        ]
    },
    {
        "id": "UL-6",
        "vendor": "Cloudflare",
        "po_number": "PO-2026-075",
        "date": "2026-01-10",
        "department": "Engineering",
        "total": "550.00",
        "line_items": [
            {"description": "Domain renewal – 1 year", "amount": "150.00", "service_period": {"start": "2026-01-01", "end": "2026-12-31"}},
            {"description": "SSL certificate – wildcard, 1 year", "amount": "400.00", "service_period": {"start": "2026-01-01", "end": "2026-12-31"}}
        ]
    },
    {
        "id": "UL-7",
        "vendor": "Sarah Chen Design LLC",
        "po_number": "PO-2026-076",
        "date": "2026-02-20",
        "department": "Marketing",
        "total": "8500.00",
        "line_items": [
            {"description": "Brand identity redesign – logo, guidelines, assets", "amount": "8500.00"}
        ]
    },
    {
        "id": "UL-8",
        "vendor": "ModernSpace Builders",
        "po_number": "PO-2026-077",
        "date": "2026-03-15",
        "department": "Operations",
        "total": "12500.00",
        "line_items": [
            {"description": "Open floor plan conversion – demolition & rebuild", "amount": "12500.00"}
        ]
    },
    {
        "id": "UL-9",
        "vendor": "Twilio Inc.",
        "po_number": "PO-2026-078",
        "date": "2026-02-28",
        "department": "Engineering",
        "total": "2500.00",
        "line_items": [
            {"description": "SMS API overage – Feb 2026 (usage-based)", "amount": "2500.00"}
        ]
    },
    {
        "id": "UL-10",
        "vendor": "Fresh Bites Catering",
        "po_number": "PO-2026-079",
        "date": "2026-03-08",
        "department": "Marketing",
        "total": "1500.00",
        "line_items": [
            {"description": "Catering – product launch celebration (75 guests)", "amount": "1500.00"}
        ]
    }
]

pos = []
for inv in invoices:
    if inv["po_number"]:
        pos.append({
            "number": inv["po_number"],
            "vendor": inv["vendor"],
            "amount": inv["total"],
            "department": inv["department"]
        })

os.makedirs("data", exist_ok=True)
with open("data/invoices.json", "w") as f:
    json.dump(invoices, f, indent=2)

with open("data/purchase_orders.json", "w") as f:
    json.dump(pos, f, indent=2)

print("Generated data fixtures.")
