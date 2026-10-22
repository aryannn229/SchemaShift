"""Migration risk score (Phase 24): explicit, testable rules over verdicts, evidence and the plan.

No number comes from a model. Every finding names its rule and links to the verdict, relationship
or query behind it. The formula is documented in SPEC.md section 25. Pure.
"""

from schemashift.risk.model import RiskCategory, RiskFinding, RiskReport
from schemashift.risk.rules import CATEGORIES, RISK_RULES, RiskContext, score_risk

__all__ = [
    "CATEGORIES",
    "RISK_RULES",
    "RiskCategory",
    "RiskContext",
    "RiskFinding",
    "RiskReport",
    "score_risk",
]
