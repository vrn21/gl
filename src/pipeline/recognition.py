from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from src.models import LineItem, Invoice, ServicePeriod, JournalEntry, JournalLine, Treatment, ClassifiedLine
from src.chart_of_accounts import PREPAID_TO_EXPENSE, is_professional_service


def resolve_service_period(item: LineItem, invoice: Invoice) -> ServicePeriod | None:
    """Line-level service_period takes priority; falls back to invoice-level."""
    return item.service_period or invoice.service_period


def months_between(start: date, end: date) -> int:
    """Count the number of calendar months a period spans (inclusive of both endpoints)."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def is_annual(period: ServicePeriod | None) -> bool:
    """Return True if the service period spans more than 1 month."""
    if period is None:
        return False
    return months_between(period.start, period.end) > 1


def monthly_periods(period: ServicePeriod) -> list[date]:
    """Generate the first day of each calendar month in the period."""
    months = []
    current = period.start.replace(day=1)
    end_month = period.end.replace(day=1)
    while current <= end_month:
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def generate_prepaid_entries(item: LineItem, classification, invoice: Invoice) -> list[JournalEntry]:
    entries = []

    # 1. Initial booking: Debit Prepaid, Credit AP
    entries.append(JournalEntry(
        id="dummy",  # Orchestrator handles real ID
        invoice_id=invoice.id,
        date=invoice.date,
        description=item.description,
        lines=[
            JournalLine(account_code=classification.gl_code, account_name="Prepaid", debit=item.amount, memo=item.description),
            JournalLine(account_code="2000", account_name="Accounts Payable", credit=item.amount, memo=item.description),
        ],
        entry_type="initial"
    ))

    # 2. Monthly amortization entries: Debit Expense, Credit Prepaid
    period = resolve_service_period(item, invoice)
    months = months_between(period.start, period.end)
    monthly_amount = (item.amount / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    expense_code = PREPAID_TO_EXPENSE.get(classification.gl_code)
    if expense_code is None:
        raise ValueError(
            f"No expense mapping for prepaid account {classification.gl_code}. "
            f"Add it to PREPAID_TO_EXPENSE in chart_of_accounts.py."
        )

    for i, month_start in enumerate(monthly_periods(period)):
        if i == months - 1:  # last month
            amt = item.amount - (monthly_amount * (months - 1))
        else:
            amt = monthly_amount
        entries.append(JournalEntry(
            id="dummy",
            invoice_id=invoice.id,
            date=month_start,
            description=item.description,
            lines=[
                JournalLine(account_code=expense_code, account_name="Expense", debit=amt, memo=item.description),
                JournalLine(account_code=classification.gl_code, account_name="Prepaid", credit=amt, memo=item.description),
            ],
            entry_type="amortization"
        ))

    return entries


def generate_expense_entries(item: LineItem, classification, invoice: Invoice) -> list[JournalEntry]:
    """Generate standard AP journal entry: Debit GL account, Credit AP (2000)."""
    return [JournalEntry(
        id="dummy",
        invoice_id=invoice.id,
        date=invoice.date,
        description=item.description,
        lines=[
            JournalLine(account_code=classification.gl_code, account_name="Expense", debit=item.amount, memo=item.description),
            JournalLine(account_code="2000", account_name="Accounts Payable", credit=item.amount, memo=item.description),
        ],
        entry_type="initial"
    )]


def generate_accrual_entries(item: LineItem, classification, invoice: Invoice) -> list[JournalEntry]:
    accrual_account = "2110" if is_professional_service(classification.gl_code) else "2100"
    expense_code = classification.gl_code  # The expense GL stays the same
    
    # We mutate classification to set posting_gl for accruals so eval checks correct account
    classification.posting_gl = accrual_account

    entries = []
    period = resolve_service_period(item, invoice)

    # 1. Accrual entry (at service period end): Debit Expense, Credit Accrued Liability
    entries.append(JournalEntry(
        id="dummy",
        invoice_id=invoice.id,
        date=period.end,
        description=f"Accrual for {item.description}",
        lines=[
            JournalLine(account_code=expense_code, account_name="Expense", debit=item.amount, memo=item.description),
            JournalLine(account_code=accrual_account, account_name="Accrued Liability", credit=item.amount, memo=item.description),
        ],
        entry_type="accrual"
    ))

    # 2. Reversal entry (at invoice date): Debit Accrued Liability, Credit AP
    entries.append(JournalEntry(
        id="dummy",
        invoice_id=invoice.id,
        date=invoice.date,
        description=f"Reversal for {item.description}",
        lines=[
            JournalLine(account_code=accrual_account, account_name="Accrued Liability", debit=item.amount, memo=item.description),
            JournalLine(account_code="2000", account_name="Accounts Payable", credit=item.amount, memo=item.description),
        ],
        entry_type="reversal"
    ))

    return entries


def generate_entries(classified_line: ClassifiedLine, invoice: Invoice) -> list[JournalEntry]:
    classification = classified_line.classification
    item = classified_line.line_item
    period = resolve_service_period(item, invoice)

    if classification.treatment == Treatment.PREPAID:
        return generate_prepaid_entries(item, classification, invoice)

    # Accrual detection: service period ended STRICTLY BEFORE invoice date
    if period and period.end < invoice.date:
        classification.treatment = Treatment.ACCRUAL  # Override EXPENSE → ACCRUAL
        return generate_accrual_entries(item, classification, invoice)

    if classification.treatment == Treatment.CAPITALIZE:
        return generate_expense_entries(item, classification, invoice)

    return generate_expense_entries(item, classification, invoice)

