"""Placement plan: how each relationship is represented in MongoDB."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel

Placement = Literal["EMBED", "REFERENCE", "REF_ARRAY"]


class Factor(FrozenModel):
    name: str
    contribution: float


class PlacementDecision(FrozenModel):
    relationship_id: str
    decision: Placement
    score: float = 0.0
    top_factors: tuple[Factor, ...] = ()
    reason: str = ""


def junction_key(junction_table: str) -> str:
    """Key under which the M:N placement of a junction table is stored."""
    return f"m2n:{junction_table}"


class PlacementPlan(FrozenModel):
    """relationship id (``fk:...`` or ``m2n:<junction>``) -> decision. Missing = REFERENCE."""

    decisions: dict[str, PlacementDecision] = {}

    def decision(self, relationship_id: str) -> Placement:
        found = self.decisions.get(relationship_id)
        return found.decision if found is not None else "REFERENCE"

    def is_embedded(self, relationship_id: str) -> bool:
        return self.decision(relationship_id) == "EMBED"

    @staticmethod
    def of(mapping: dict[str, Placement]) -> PlacementPlan:
        return PlacementPlan(
            decisions={
                k: PlacementDecision(relationship_id=k, decision=v) for k, v in mapping.items()
            }
        )
