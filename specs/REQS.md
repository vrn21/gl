# Take-Home Assessment

**Time:** 24 hours

## Scenario

Build an AI agent that automates an Accounts Payable workflow: receive vendor invoices,
match to purchase orders, classify each line item to the correct GL account, handle
prepaid/accrual recognition, check approval thresholds, and post journal entries. The agent
must follow the SOP below exactly.

## Chart of Accounts

```
Code Account Category
1300 Prepaid Expenses (General) Prepaid
1310 Prepaid Software Prepaid
1320 Prepaid Insurance Prepaid
1500 Fixed Assets Asset
2100 Accrued Expenses (General) Accrual
2110 Accrued Professional Services Accrual
5000 Office Supplies Expense
```

```
5010 Software & Subscriptions Expense
5020 Cloud Hosting & Infrastructure Expense
5030 Professional Services — Legal Expense
5040 Professional Services — Consulting Expense
5050 Marketing & Advertising Expense
5060 Travel & Entertainment Expense
5070 Facilities & Maintenance Expense
5080 Training & Development Expense
5090 Telecom & Internet Expense
5100 Insurance Expense Expense
5110 Equipment (under $5,000) Expense
```
## GL Classification SOP

### Step 1 — PO Matching

```
● Match every invoice to a PO by number. Validate amount within 10% tolerance.
● No PO → flag for manual review, do not classify.
```
### Step 2 — Line-Item Classification (priority order)

1. **Physical goods:** Office supplies, stationery, toner → **5000**. Branded merch (t-shirts,
    swag) → **5000** even if from Marketing.
2. **Equipment:** Unit cost < $5K → **5110**. Unit cost ≥ $5K → **1500** (capitalize).


3. **Software/SaaS:** Monthly/usage → **5010**. Annual prepayment → **1310** (prepaid).
4. **Cloud hosting:** Monthly → **5020**. Annual prepayment → **1300** (amortize to 5020).
5. **Professional services:** Legal (litigation, patent, contract, regulatory) → **5030**.
    Consulting/advisory → **5040**. Mixed legal+non-legal → **5030** (conservative).
6. **Marketing:** Ad spend, campaigns, sponsorships → **5050**. Exception: branded physical
    merch → **5000** (rule 1).
7. **Other:** Travel/meals → **5060**. Facilities/maintenance → **5070**. Training/conferences →
    **5080**. Telecom/internet → **5090**. Insurance → see Step 3.

### Step 3 — Prepaid & Accrual Recognition

**Prepaid:** Service period > 1 month + paid upfront → book to Prepaid account, amortize monthly
to the expense account. Insurance → 1320, amortize to 5100.
**Accrual:** Invoice received after service period ends → book accrual entry (2110 for professional
services, 2100 for all others). Reverse when paid.

### Step 4 — Approval Routing (invoice level)

**Base:** ≤ $1K auto-approve | $1K–$10K dept manager | > $10K VP Finance. Any Fixed Asset
(1500) → VP Finance regardless.
**Overrides:** Marketing auto-approve up to $2.5K. Engineering auto-approve up to $5K if all lines
are Cloud (5020) or Software (5010).
No matching rule → deny (fail closed).

### Step 5 — Post & Verify

Post journal entry. Verify line items sum to invoice total. Flag unclassifiable items for human
review.

## Sample Invoices (Labeled)

These 6 invoices are your test cases. The expected GL classification for each line item is
provided — use these to validate your agent.

### INV-001 — Cloudware Solutions

PO: PO-2026-044 | Date: Jan 5, 2026 | Dept: Engineering | Total: $24,


```
Line Item Amoun
t
Expected
GL
Treatment
Annual Platform License (Jan–Dec
2026)
$24,
0
1310 Prepaid — amortize $2K/mo to
5010
```
### INV-002 — Morrison & Burke LLP

PO: PO-2026-051 | Date: Jan 20, 2026 | Dept: Legal | Total: $9,
**Line Item Amount Expected GL Treatment**
Patent filing & prosecution $4,500 5030 Expense
Regulatory compliance review & advisory $3,200 5040 Expense
Contract review for marketing partnership $1,800 5030 Expense

### INV-003 — TechDirect Inc.

PO: PO-2026-038 | Date: Feb 1, 2026 | Dept: Engineering | Total: $49,
**Line Item Amou
nt
Expected
GL
Treatment**
MacBook Pro 14" (3x $1,800) $5,400 5110 Expense (unit cost < $5K)
Dell PowerEdge R760 Server $8,500 1500 Capitalize (unit cost ≥ $5K)
AWS Reserved Instances Annual (Feb
26–Jan 27)
$36,
0
1300 Prepaid — amortize $3K/mo to
5020

### INV-004 — Apex Strategy Group


PO: PO-2025-189 | Date: Jan 15, 2026 | Service period: Dec 2025 | Dept: Operations |
Total: $8,
**Line Item Amou
nt
Expected
GL
Treatment**
Operational efficiency assessment – Dec
2025
$7,500 2110 Accrual — service before invoice
date
Travel expenses – Dec 2025 on-site
visits
$1,200 2100 Accrual — service before invoice
date

### INV-005 — BrightSpark Agency

PO: PO-2026-062 | Date: Feb 10, 2026 | Dept: Marketing | Total: $23,
**Line Item Amount Expected GL Treatment**
Q1 digital ad campaign management $15,000 5050 Expense
Branded company t-shirts (500 units) $2,000 5000 Expense (physical goods)
Conference booth rental – Mar 2026 $5,000 5050 Expense
Conference attendee gift bags (branded) $1,500 5000 Expense (physical goods)

### INV-006 — QuickPrint Co.

PO: _none_ | Date: Feb 20, 2026 | Dept: Marketing | Total: $3,
**Line Item Amoun
t
Expected
GL
Treatment**
Rush printing – sales brochures (5,
units)
$3,800 — Exception: no PO, flag for
review


## Requirements

Build an agent that takes a vendor invoice as input and produces the correct journal entries as
output, following the SOP above. The system should:
● Process all 6 sample invoices correctly — including PO matching, line-item classification,
prepaid amortization schedules, accrual entries with reversals, and exception handling
● Route each invoice through the correct approval path before posting
● Support a **dry-run mode** — show what would be posted without actually posting
● Support a **human-in-the-loop** flow — when approval is required, pause and provide a
way to resume
Your agent won't be perfect on day one. Also build:
● An **eval system** that measures classification, treatment, and approval accuracy across
the 6 labeled invoices
● A **shadow mode** that processes the 10 unlabeled invoices below without posting,
capturing proposals for human review
● A **feedback loop** — collect corrections, identify error patterns, refine your rules, and
demonstrate measurable improvement (before/after accuracy)
How you structure the code, what data models you use, and how you decompose the problem
is up to you. SQLite or JSON files are fine for storage.

## Unlabeled Invoices (for Eval & Feedback Loop)

```
ID Vendor PO Date Dept Line Items Total
UL-
1
DataSync Pro PO-
6-
Feb 15,
2026
Engineeri
ng
```
1. Annual Platform License (Feb
26–Jan 27) $18,
2. Premium Support &
Implementation Services $6,
    $24,
    00


```
UL-
2
WeWork PO-
6-
Mar 1,
2026
Operation
s
```
1. Monthly hot desk membership
- 15 seats, Mar 2026 $4,
    $4,
    0
UL-
3
TalentBridge
Partners
PO-
6-
Feb 28,
2026
Engineeri
ng
1. Placement fee – Senior
Software Engineer $25,
$25,
00
UL-
4
Global Tech
Summit
PO-
6-
Mar 5,
2026
Engineeri
ng
1. Conference registration (
attendees) $3,
2. Flight + hotel – SF, 3 nights
$3,
$6,
0
UL-
5
RenewTech PO-
6-
Mar 10,
2026
Operation
s
1. Refurbished Dell 27" monitors
(10x $450) $4,
$4,
0
UL-
6
Cloudflare PO-
6-
Jan 10,
2026
Engineeri
ng
1. Domain renewal – 1 year $
2. SSL certificate – wildcard, 1
year $
$
UL-
7
Sarah Chen
Design LLC
PO-
6-
Feb 20,
2026
Marketing 1. Brand identity redesign – logo,
guidelines, assets $8,
$8,
0
UL-
8
ModernSpace
Builders
PO-
6-
Mar 15,
2026
Operation
s
1. Open floor plan conversion –
demolition & rebuild $12,
$12,
00
UL-
9
Twilio Inc. PO-
6-
Feb 28,
2026
Engineeri
ng
1. SMS API overage – Feb 2026
(usage-based) $2,
$2,
0
UL-
10
Fresh Bites
Catering
PO-
6-
Mar 8,
2026
Marketing 1. Catering – product launch
celebration (75 guests) $1,
$1,
0

## Deliverables

1. **Working code** — GitHub repo (public or invite us)
2. **README** — setup instructions, architecture decisions, tradeoffs



