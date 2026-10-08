"""Equivalence checker: decides which SQL guarantees MongoDB preserves."""

from schemashift.equivalence.checker import (
    EquivalenceReport,
    EquivalenceResult,
    PlacementChange,
    check_both,
    check_program,
)
from schemashift.equivalence.context import RuleContext, RuleOptions

__all__ = [
    "EquivalenceReport",
    "EquivalenceResult",
    "PlacementChange",
    "RuleContext",
    "RuleOptions",
    "check_both",
    "check_program",
]
