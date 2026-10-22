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
        + f.worst_child_count * f.estimated_child_doc_size_bytes * w.hard.safety_factor
    )


def hard_constraint(f: RelationshipFeatures, w: Weights) -> tuple[str, str] | None:
    """Return ``(name, explanation)`` when a hard constraint forces REFERENCE."""
    if f.cardinality == "self":
        return (
            "self_reference",
            "self-referencing relationships form a hierarchy and are never embedded",
        )
    if f.child_fk_nullable:
        return (
            "optional_parent",
            "the foreign key is nullable: a child row without a parent would have no document "
            "to live in and would be lost on embedding",
        )
    if f.worst_child_count > w.hard.max_children_per_parent:
        measured = f.max_child_count_per_parent > f.estimated_child_count_per_parent
        who = (
            f"one parent has {f.worst_child_count} children, which"
            if measured
            else f"about {f.worst_child_count} children per parent"
        )
        return (
            "unbounded_growth",
            f"{who} exceeds the {w.hard.max_children_per_parent} limit (unbounded array growth)",
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


def workload_terms(f: RelationshipFeatures, w: Weights) -> dict[str, float]:
    """Penalties from the workload and seed statistics (all <= 0; empty when neither says anything).

    * parent_write_amplification: every write to the parent rewrites a document that holds its embedded
      children; weighted by the parent's share of the workload and by how big the children make it
      (children bytes / ``doc_budget_bytes``, capped at 1);
    * child_write_share: the child's share of the workload's statements that write (positional updates
      into a parent document);
    * fanout_skew: a few parents with far more children than the average make an embedded array unsafe
      even when the average looks small.
    """
    k = w.workload
    out: dict[str, float] = {}
    if f.workload_weighted:
        children_bytes = f.estimated_child_count_per_parent * f.estimated_child_doc_size_bytes
        amplification = f.parent_write_share * min(1.0, children_bytes / k.doc_budget_bytes)
        out["parent_write_amplification"] = k.parent_write_amplification * amplification
        out["child_write_share"] = k.child_write_share * min(1.0, f.child_write_share)
    if f.fanout_skew > 0:
        out["fanout_skew"] = k.fanout_skew * min(1.0, f.fanout_skew / k.skew_scale)
    return out


def score_relationship(f: RelationshipFeatures, w: Weights) -> CostResult:
    contributions = {**soft_factors(f, w), **workload_terms(f, w)}
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
