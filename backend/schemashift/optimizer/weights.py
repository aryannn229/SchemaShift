"""Cost-model weights, loaded from ``weights.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

from schemashift.models.base import FrozenModel

WeightsPath = Path(__file__).with_name("weights.yaml")


class HardLimits(FrozenModel):
    max_parent_doc_bytes: int = 4_194_304
    safety_factor: float = 2
    max_children_per_parent: int = 1000
    small_children: int = 50
    max_embed_depth: int = 3


class SoftWeights(FrozenModel):
    read_together: float = 0.30
    shape: float = 0.20
    guarantees: float = 0.20
    independent_access: float = -0.20
    write_frequency: float = -0.10


class WorkloadWeights(FrozenModel):
    """Workload terms (Phase 18). All are penalties (<= 0) added to the raw score when the workload
    or the
    seed data says something the defaults cannot: they never change a plan that has neither."""

    parent_write_amplification: float = (
        -0.15
    )  # parent writes rewrite a bigger document when children are embedded
    child_write_share: float = -0.10  # share of the workload that writes the child
    fanout_skew: float = -0.10  # a few parents with many more children than the average
    doc_budget_bytes: int = (
        262_144  # document size at which rewriting it on every parent write hurts
    )
    skew_scale: float = 10.0  # (max - avg) / avg at which the skew penalty saturates


class ShapeValues(FrozenModel):
    one_to_one: float = 1.0
    one_to_many_small: float = 0.6
    other: float = 0.0


class WriteNorm(FrozenModel):
    low: float = 0.0
    med: float = 0.5
    high: float = 1.0


class Defaults(FrozenModel):
    child_independent_access: float = 0.3
    read_together_ratio: float = 0.5
    child_write_frequency: Literal["low", "med", "high"] = "med"
    children_per_parent_one_to_one: int = 1
    children_per_parent_one_to_many: int = 20


class Weights(FrozenModel):
    threshold: float = 0.5
    hard: HardLimits = HardLimits()
    soft: SoftWeights = SoftWeights()
    workload: WorkloadWeights = WorkloadWeights()
    shape: ShapeValues = ShapeValues()
    write_norm: WriteNorm = WriteNorm()
    defaults: Defaults = Defaults()

    @property
    def raw_min(self) -> float:
        """Lowest possible raw score (all penalties at 1, all bonuses at 0)."""
        return sum(w for w in self._weights() if w < 0)

    @property
    def raw_max(self) -> float:
        return sum(w for w in self._weights() if w > 0)

    def _weights(self) -> list[float]:
        s = self.soft
        return [s.read_together, s.shape, s.guarantees, s.independent_access, s.write_frequency]


@lru_cache
def load_weights(path: Path | None = None) -> Weights:
    data = yaml.safe_load((path or WeightsPath).read_text(encoding="utf-8")) or {}
    return Weights.model_validate(data)
