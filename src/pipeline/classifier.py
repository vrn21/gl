import re
from dataclasses import dataclass
from typing import Callable

from src.models import LineItem, Invoice, Classification, Treatment, ServicePeriod


@dataclass
class ClassificationRule:
    name: str
    priority: int
    fn: Callable[[LineItem, Invoice], Classification | None]


# Shared utilities (these could live in recognition.py, but are needed here for Rule 3 and 4)
def months_between(start, end) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def is_annual(period: ServicePeriod | None) -> bool:
    if period is None:
        return False
    return months_between(period.start, period.end) > 1


def resolve_service_period(item: LineItem, invoice: Invoice) -> ServicePeriod | None:
    return item.service_period or invoice.service_period


# Rules

def classify_physical_goods(item: LineItem, invoice: Invoice) -> Classification | None:
    desc = item.description.lower()
    substring_keywords = ["office suppl", "stationery", "toner", "paper",
                          "t-shirt", "swag", "gift bag", "branded", "merch"]
    regex_keywords = [r"\bpen\b", r"\bpens\b"]
    if any(kw in desc for kw in substring_keywords) or any(re.search(pat, desc) for pat in regex_keywords):
        return Classification(gl_code="5000", treatment=Treatment.EXPENSE,
                              rule_applied="Physical goods → 5000")
    return None


def classify_equipment(item: LineItem, invoice: Invoice) -> Classification | None:
    substring_keywords = ["laptop", "macbook", "monitor", "printer",
                          "desktop", "workstation", "projector",
                          "scanner", "keyboard", "mouse", "headset"]
    regex_keywords = [r"\bserver\b(?! rental)", r"\bphone\b(?! plan)",
                      r"\bswitch\b", r"\brouter\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    
    if not (has_substring or has_regex):
        return None
        
    unit_cost = item.unit_cost or item.amount
    if unit_cost >= 5000:
        return Classification(gl_code="1500", treatment=Treatment.CAPITALIZE,
                              rule_applied=f"Equipment unit cost ${unit_cost} ≥ $5K → 1500")
    return Classification(gl_code="5110", treatment=Treatment.EXPENSE,
                          rule_applied=f"Equipment unit cost ${unit_cost} < $5K → 5110")


def classify_software(item: LineItem, invoice: Invoice) -> Classification | None:
    substring_keywords = ["software", "saas", "subscription", "platform", "suite", "module",
                          "domain", "ssl", "certificate"]
    regex_keywords = [r"\bapp\b", r"\btool\b", r"\blicense\b"]
    desc = item.description.lower()
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    
    if not (has_substring or has_regex):
        return None
        
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1310", treatment=Treatment.PREPAID,
                              rule_applied="Software annual prepayment → 1310")
    return Classification(gl_code="5010", treatment=Treatment.EXPENSE,
                          rule_applied="Software monthly/usage → 5010")


def classify_cloud(item: LineItem, invoice: Invoice) -> Classification | None:
    cloud_keywords = ["cloud", "hosting", "aws", "azure", "gcp",
                      "cloud infrastructure", "cloud storage", "object storage",
                      "compute", "cdn", "server rental", "reserved instance",
                      "api overage", "api usage"]
    if not any(kw in item.description.lower() for kw in cloud_keywords):
        return None
        
    period = resolve_service_period(item, invoice)
    if is_annual(period):
        return Classification(gl_code="1300", treatment=Treatment.PREPAID,
                              rule_applied="Cloud annual prepayment → 1300")
    return Classification(gl_code="5020", treatment=Treatment.EXPENSE,
                          rule_applied="Cloud monthly/usage → 5020")


def classify_professional_services(item: LineItem, invoice: Invoice) -> Classification | None:
    prof_service_keywords = ["legal", "law ", "attorney", "counsel", "consulting",
                             "advisory", "assessment", "litigation", "patent",
                             "prosecution", "placement", "recruiting", "staffing",
                             "contract review", "contract draft"]
    desc = item.description.lower()
    if not any(kw in desc for kw in prof_service_keywords):
        return None

    legal_action_keywords = ["litigation", "patent", "contract review", "prosecution",
                             "contract draft", "filing"]
    consulting_keywords = ["advisory", "consulting", "assessment", "analysis", "strategy",
                           "implementation", "optimization", "placement", "recruiting",
                           "staffing"]

    has_legal = any(kw in desc for kw in legal_action_keywords)
    has_consulting = any(kw in desc for kw in consulting_keywords)

    if has_legal and has_consulting:
        return Classification(gl_code="5030", treatment=Treatment.EXPENSE,
                              rule_applied="Mixed legal+consulting → 5030 (conservative)")
    if has_legal:
        return Classification(gl_code="5030", treatment=Treatment.EXPENSE,
                              rule_applied="Legal services → 5030")
    if has_consulting:
        return Classification(gl_code="5040", treatment=Treatment.EXPENSE,
                              rule_applied="Consulting/advisory → 5040")
    return None


def classify_marketing(item: LineItem, invoice: Invoice) -> Classification | None:
    desc = item.description.lower()
    substring_keywords = ["advertising", "campaign", "sponsorship", "media buy",
                          "promotion", "seo", "ppc", "social media",
                          "booth", "exhibition", "brand identity", "redesign",
                          "creative", "design services"]
    regex_keywords = [r"\bad\b", r"\bads\b"]
    has_substring = any(kw in desc for kw in substring_keywords)
    has_regex = any(re.search(pat, desc) for pat in regex_keywords)
    
    if not (has_substring or has_regex):
        return None
        
    return Classification(gl_code="5050", treatment=Treatment.EXPENSE,
                          rule_applied="Marketing/advertising → 5050")


def classify_other(item: LineItem, invoice: Invoice) -> Classification | None:
    desc = item.description.lower()
    other_rules = [
        (["travel", "flight", "hotel", "lodging", "airfare", "uber", "taxi", "meal",
          "per diem", "catering"], "5060", "Travel & entertainment → 5060"),
        (["maintenance", "facilities", "janitorial", "hvac", "plumbing", "repair",
          "renovation", "construction", "conversion", "remodel", "demolition",
          "coworking", "co-working", "hot desk", "workspace", "office rental",
          "desk membership"],
         "5070", "Facilities & maintenance → 5070"),
        (["training", "workshop", "certification", "course", "seminar",
          "conference registration"],
         "5080", "Training & development → 5080"),
        (["telecom", "internet", "phone plan", "mobile", "broadband", "sms"],
         "5090", "Telecom & internet → 5090"),
        (["insurance", "policy", "coverage", "premium"],
         "5100", "Insurance → 5100"),
    ]
    
    for keywords, gl_code, rule_name in other_rules:
        if any(kw in desc for kw in keywords):
            if gl_code == "5100" and is_annual(resolve_service_period(item, invoice)):
                return Classification(gl_code="1320", treatment=Treatment.PREPAID,
                                      rule_applied="Insurance annual prepayment → 1320")
            return Classification(gl_code=gl_code, treatment=Treatment.EXPENSE,
                                  rule_applied=rule_name)
    return None


RULES: list[ClassificationRule] = [
    ClassificationRule(name="physical_goods",          priority=1, fn=classify_physical_goods),
    ClassificationRule(name="equipment",               priority=2, fn=classify_equipment),
    ClassificationRule(name="software_saas",           priority=3, fn=classify_software),
    ClassificationRule(name="cloud_hosting",            priority=4, fn=classify_cloud),
    ClassificationRule(name="professional_services",   priority=5, fn=classify_professional_services),
    ClassificationRule(name="marketing",               priority=6, fn=classify_marketing),
    ClassificationRule(name="other",                   priority=7, fn=classify_other),
]

def classify_line_item(item: LineItem, invoice: Invoice) -> Classification | None:
    """Apply rules in priority order. First match wins."""
    for rule in sorted(RULES, key=lambda r: r.priority):
        result = rule.fn(item, invoice)
        if result is not None:
            return result
    return None
