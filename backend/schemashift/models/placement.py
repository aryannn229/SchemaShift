"""Placement plan: how each relationship is represented in MongoDB."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from schemashift.models.base import FrozenModel

Placement = Literal["EMBED", "REFERENCE", "REF_ARRAY"]


class Factor(FrozenModel):
    name: str
    contribution: float


class AISuggestion(FrozenModel):
    """The LLM's advisory opinion (never overrides the optimizer)."""

    decision: Literal["EMBED", "REFERENCE"]
    confidence: float = Field(ge=0.0, le=1.0)
    justification: str


class AIOpinion(FrozenModel):
    source: Literal["anthropic", "mock", "unavailable"]
    available: bool  # False for the mock advisor and for failed calls
    suggestion: AISuggestion | None = None
    error: str | None = None


class PlacementDecision(FrozenModel):
    relationship_id: str
    decision: Placement
    score: float = 0.0
    top_factors: tuple[Factor, ...] = ()
    reason: str = ""
    overridden: bool = False  # forced by the user (relationship_overrides)
    host: str | None = None  # table whose document hosts the embedding / array of references
    ai: AIOpinion | None = None  # advisory second opinion
    agree: bool | None = None  # optimizer == AI (only when a real AI opinion exists)


def junction_key(junction_table: str) -> str:
    """Key under which the M:N placement of a junction table is stored."""
    return f"m2n:{junction_table}"


class PlacementPlan(FrozenModel):
    """relationship id (``fk:...`` or ``m2n:<junction>``) -> decision. Missing = REFERENCE."""

    decisions: dict[str, PlacementDecision] = {}
    warnings: tuple[str, ...] = ()

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
