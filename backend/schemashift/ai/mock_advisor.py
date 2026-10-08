"""Deterministic advisor used in tests and whenever no API key is configured."""

from __future__ import annotations

from schemashift.models.placement import AISuggestion
from schemashift.optimizer.features import RelationshipFeatures


class MockAdvisor:
    name = "mock"
    available = False  # the UI shows "AI advisor disabled" and hides this opinion
    model = "mock"

    def suggest_placement(self, rel: RelationshipFeatures, schema_excerpt: str) -> AISuggestion:
        """A simple rule of thumb: small, read-together, single-parent children embed."""
        embed = (
            not rel.child_has_other_parents
            and rel.estimated_child_count_per_parent <= 50
            and (rel.cardinality == "1:1" or rel.read_together_ratio >= 0.5)
            and rel.cardinality != "self"
        )
        return AISuggestion(
            decision="EMBED" if embed else "REFERENCE",
            confidence=0.6,
            justification=(
                "mock advisor: small single-parent child read together with its parent"
                if embed
                else "mock advisor: default to a reference"
            ),
        )
