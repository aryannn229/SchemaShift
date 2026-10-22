"""Equivalence checker: decides which SQL guarantees MongoDB preserves."""

from schemashift.equivalence.checker import (
    ConcurrencyReport,
    EquivalenceReport,
    EquivalenceResult,
    PlacementChange,
    check_both,
    check_concurrency,
    check_program,
)
from schemashift.equivalence.context import RuleContext, RuleOptions

__all__ = [
    "ConcurrencyReport",
    "EquivalenceReport",
    "EquivalenceResult",
    "PlacementChange",
    "RuleContext",
    "RuleOptions",
    "check_both",
    "check_concurrency",
    "check_program",
]
