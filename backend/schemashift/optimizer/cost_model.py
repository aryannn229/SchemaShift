"""Embed-vs-reference scoring for one relationship."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.placement import Factor
from schemashift.optimizer.features import RelationshipFeatures
from schemashift.optimizer.weights import Weights


class CostResult(FrozenModel):
    relationship_id: str
    score: float  # normalized embed score in [0, 1]
    raw: float
    decision: Literal["EMBED", "REFERENCE"]
    hard_constraint: str | None = None  # name of the hard constraint that forced REFERENCE
    reason: str = ""
    factors: tuple[Factor, ...] = ()  # all soft factors, strongest first


def projected_parent_bytes(f: RelationshipFeatures, w: Weights) -> float:
    """Parent document size if every child were embedded (with the safety factor)."""
    return (
        f.parent_doc_size_bytes
        + f.estimated_child_count_per_parent
        * f.estimated_child_doc_size_bytes
        * w.hard.safety_factor
    )


def hard_constraint(f: RelationshipFeatures, w: Weights) -> tuple[str, str] | None:
    """Return ``(name, explanation)`` when a hard constraint forces REFERENCE."""
    if f.cardinality == "self":
        return (
            "self_reference",
            "self-referencing relationships form a hierarchy and are never embedded",
        )
    if f.estimated_child_count_per_parent > w.hard.max_children_per_parent:
        return (
            "unbounded_growth",
            f"about {f.estimated_child_count_per_parent} children per parent exceeds the "
            f"{w.hard.max_children_per_parent} limit (unbounded array growth)",
        )
    size = projected_parent_bytes(f, w)
    if size > w.hard.max_parent_doc_bytes:
        return (
            "document_size",
            f"projected parent document of {size / 1_048_576:.1f} MB exceeds the "
            f"{w.hard.max_parent_doc_bytes / 1_048_576:.0f} MB ceiling",
        )
    return None


def soft_factors(f: RelationshipFeatures, w: Weights) -> dict[str, float]:
    """The signed contribution of each soft factor to the raw score."""
    if f.cardinality == "1:1":
        shape = w.shape.one_to_one
    elif f.cardinality == "1:N" and f.estimated_child_count_per_parent <= w.hard.small_children:
        shape = w.shape.one_to_many_small
    else:
        shape = w.shape.other
    write = getattr(w.write_norm, f.child_write_frequency)
    s = w.soft
    return {
        "read_together_ratio": s.read_together * f.read_together_ratio,
        "relationship_shape": s.shape * shape,
        "guarantees_needing_embedding": s.guarantees * min(f.guarantees_needing_embedding, 2) / 2,
        "child_independent_access": s.independent_access * f.child_independent_access,
        "child_write_frequency": s.write_frequency * write,
    }


def score_relationship(f: RelationshipFeatures, w: Weights) -> CostResult:
    contributions = soft_factors(f, w)
    raw = sum(contributions.values())
    span = w.raw_max - w.raw_min
    score = min(1.0, max(0.0, (raw - w.raw_min) / span)) if span else 0.5
    factors = tuple(
        Factor(name=name, contribution=round(value, 4))
        for name, value in sorted(contributions.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
        if value != 0
    )
    hard = hard_constraint(f, w)
    if hard is not None:
        return CostResult(
            relationship_id=f.relationship_id,
            score=round(score, 4),
            raw=round(raw, 4),
            decision="REFERENCE",
            hard_constraint=hard[0],
            reason=f"hard constraint: {hard[1]}",
            factors=factors,
        )
    embed = score >= w.threshold
    reason = f"embed score {score:.2f} {'>=' if embed else '<'} threshold {w.threshold:.2f}"
    return CostResult(
        relationship_id=f.relationship_id,
        score=round(score, 4),
        raw=round(raw, 4),
        decision="EMBED" if embed else "REFERENCE",
        reason=reason,
        factors=factors,
    )
