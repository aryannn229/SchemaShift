"""Sensitivity of the embed/reference decisions to the tunable weights.

Every weight in ``weights.yaml`` is perturbed by -20%, -10%, +10% and +20% (one at a time), the
plan is
recomputed from the same features, and any relationship whose decision flips is reported with the
weight
and the change that flipped it. A decision that flips under a 10% change is **fragile**; one that
only
flips at 20% is **watch**; one that never flips is **stable**; one fixed by a hard constraint or a
user
override cannot flip and is **constrained**. The analysis is deterministic: the same schema,
workload and
weights give the same report.
"""

from __future__ import annotations

from schemashift.models.base import FrozenModel
from schemashift.models.placement import Flip, Fragility, PlacementPlan
from schemashift.optimizer.cost_model import hard_constraint
from schemashift.optimizer.features import RelationshipFeatures
from schemashift.optimizer.planner import OptimizerOptions, plan_from_features
from schemashift.optimizer.weights import Weights
from schemashift.semantic.graph import SchemaGraph

DELTAS: tuple[float, ...] = (-0.2, -0.1, 0.1, 0.2)
WEIGHT_NAMES: tuple[str, ...] = (
    "threshold",
    "soft.read_together",
    "soft.shape",
    "soft.guarantees",
    "soft.independent_access",
    "soft.write_frequency",
    "workload.parent_write_amplification",
    "workload.child_write_share",
    "workload.fanout_skew",
)


class SensitivityReport(FrozenModel):
    deltas: tuple[float, ...]
    weights: tuple[str, ...]
    fragility: dict[str, Fragility]

    @property
    def fragile(self) -> list[str]:
        return sorted(r for r, f in self.fragility.items() if f.level == "fragile")


def perturb(w: Weights, name: str, delta: float) -> Weights:
    """A copy of ``w`` with one weight scaled by ``1 + delta``."""
    if "." not in name:
        return w.model_copy(update={name: getattr(w, name) * (1 + delta)})
    group, field = name.split(".")
    inner = getattr(w, group)
    return w.model_copy(
        update={group: inner.model_copy(update={field: getattr(inner, field) * (1 + delta)})}
    )


def analyze_sensitivity(
    graph: SchemaGraph,
    feats: dict[str, RelationshipFeatures],
    options: OptimizerOptions,
    weights: Weights,
    base: PlacementPlan,
    deltas: tuple[float, ...] = DELTAS,
    names: tuple[str, ...] = WEIGHT_NAMES,
) -> SensitivityReport:
    """Re-plan with each weight perturbed and record which decisions flip."""
    flips: dict[str, list[Flip]] = {rid: [] for rid in feats}
    for name in names:
        for delta in deltas:
            plan = plan_from_features(graph, feats, options, perturb(weights, name, delta))
            for rid in feats:
                before, after = base.decision(rid), plan.decision(rid)
                if before != after:
                    flips[rid].append(Flip(weight=name, change=delta, becomes=after))
    out: dict[str, Fragility] = {}
    for rid, f in feats.items():
        fixed = rid in options.relationship_overrides or hard_constraint(f, weights) is not None
        found = tuple(flips[rid])
        if fixed and not found:
            level = "constrained"
        elif any(abs(x.change) <= 0.1 + 1e-9 for x in found):
            level = "fragile"
        elif found:
            level = "watch"
        else:
            level = "stable"
        out[rid] = Fragility(level=level, flips=found)
    return SensitivityReport(deltas=deltas, weights=names, fragility=out)


def attach_fragility(plan: PlacementPlan, report: SensitivityReport) -> PlacementPlan:
    """The plan with each relationship's fragility recorded on its decision."""
    decisions = {
        rid: d.model_copy(update={"fragility": report.fragility.get(rid)})
        for rid, d in plan.decisions.items()
    }
    return plan.model_copy(update={"decisions": decisions})
