"""The advisor interface. Advisors are advisory only: the optimizer's decision is final."""

from __future__ import annotations

from typing import Protocol

from schemashift.models.placement import AISuggestion
from schemashift.optimizer.features import RelationshipFeatures


class AdvisorError(Exception):
    """The advisor could not produce a valid suggestion (after its retry)."""


class LLMAdvisor(Protocol):
    name: str  # "anthropic" | "mock"
    available: bool  # False for the deterministic mock (UI shows "AI advisor disabled")
    model: str  # part of the cache key

    def suggest_placement(self, rel: RelationshipFeatures, schema_excerpt: str) -> AISuggestion:
        """Embed or reference? Raises ``AdvisorError`` when no valid answer was obtained."""
        ...


__all__ = ["AISuggestion", "AdvisorError", "LLMAdvisor"]
