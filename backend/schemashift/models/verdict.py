"""Verdict models produced by the equivalence checker."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.source import SourceSpan

Status = Literal["SAFE", "CHANGED", "BROKEN"]
SEVERITY_ORDER: dict[Status, int] = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}


class ConditionTrace(FrozenModel):
    """One condition a rule evaluated and its result (the UI's "Why?" view)."""

    condition: str
    result: bool
    detail: str | None = None


class Verdict(FrozenModel):
    node_id: str
    rule_id: str
    ir_node_type: str
    status: Status
    outcome: str  # which declared outcome branch fired
    reason: str
    mitigation: str | None = None
    mongo_feature: str | None = None
    conditions_evaluated: tuple[ConditionTrace, ...] = ()
    source_span: SourceSpan | None = None


def worst(statuses: list[Status]) -> Status:
    """Overall verdict: BROKEN if any BROKEN, else CHANGED if any CHANGED, else SAFE."""
    result: Status = "SAFE"
    for s in statuses:
        if SEVERITY_ORDER[s] > SEVERITY_ORDER[result]:
            result = s
    return result
