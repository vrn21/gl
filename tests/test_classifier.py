from decimal import Decimal
import pytest
from datetime import date

from src.models import Invoice, LineItem, ServicePeriod, Treatment
from src.pipeline.classifier import classify_line_item

def make_invoice(desc: str, amount: Decimal = Decimal("100"), unit_cost: Decimal | None = None, dept: str = "Engineering", service_period: ServicePeriod | None = None) -> (LineItem, Invoice):
    item = LineItem(description=desc, amount=amount, unit_cost=unit_cost)
    inv = Invoice(
        id="INV-001",
        vendor="Test Vendor",
        po_number=None,
        date=date(2026, 1, 1),
        department=dept,
        line_items=[item],
        total=amount,
        service_period=service_period
    )
    return item, inv

def test_physical_goods():
    item, inv = make_invoice("office supplies")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5000"
    
def test_branded_merch_marketing():
    item, inv = make_invoice("Branded company t-shirts", dept="Marketing")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5000"

def test_equipment_expense():
    item, inv = make_invoice("MacBook Pro", unit_cost=Decimal("1800"))
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5110"
    assert cls.treatment == Treatment.EXPENSE

def test_equipment_capitalize():
    item, inv = make_invoice("Dell PowerEdge R760 Server", unit_cost=Decimal("8500"))
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "1500"
    assert cls.treatment == Treatment.CAPITALIZE

def test_annual_platform_license():
    period = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    item, inv = make_invoice("Annual Platform License", service_period=period)
    # The line item has service period, so we set it on the line item
    item.service_period = period
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "1310"
    assert cls.treatment == Treatment.PREPAID

def test_monthly_saas():
    item, inv = make_invoice("Monthly SaaS subscription")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5010"

def test_patent_filing_prosecution():
    item, inv = make_invoice("Patent filing & prosecution")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5030"

def test_regulatory_compliance_review_advisory():
    item, inv = make_invoice("Regulatory compliance review & advisory")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5040"

def test_contract_review_marketing():
    item, inv = make_invoice("Contract review for marketing partnership")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5030"

def test_aws_reserved_instances():
    period = ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31))
    item, inv = make_invoice("AWS Reserved Instances Annual", service_period=period)
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "1300"
    assert cls.treatment == Treatment.PREPAID

def test_sms_api_overage():
    item, inv = make_invoice("SMS API overage")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5020"

def test_ad_campaign():
    item, inv = make_invoice("Q1 digital ad campaign management")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5050"

def test_conference_booth():
    item, inv = make_invoice("Conference booth rental")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5050"

def test_conference_registration():
    item, inv = make_invoice("Conference registration")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5080"

def test_travel_expenses():
    item, inv = make_invoice("Travel expenses")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5060"

def test_unclassifiable_returns_none():
    item, inv = make_invoice("Random weird expense")
    cls = classify_line_item(item, inv)
    assert cls is None

def test_server_rental_negative_lookahead():
    item, inv = make_invoice("server rental")
    cls = classify_line_item(item, inv)
    assert cls.gl_code == "5020" # Not equipment (1500/5110)
