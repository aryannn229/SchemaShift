"""Results of running the generated helpers from several threads against the replica set."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel

Strategy = Literal["generated", "unguarded"]
Kind = Literal["fk_race", "duplicate_insert", "counter", "transaction"]


class ConcurrencyResult(FrozenModel):
    """One probe: ``iterations`` racing rounds of ``threads`` writers with a given strategy.

    ``generated`` runs the helpers exactly as shipped; ``unguarded`` switches their concurrency guards
    (lock documents, the atomic array guard) off, or drops the transaction, to show what the same
    operations would let through. Zero violations is *test evidence*, not proof; a nonzero count is a
    demonstration, reproducible from ``seed``.
    """

    scenario_id: str  # the concurrency verdict this probe backs up ("conc:...")
    kind: Kind
    strategy: Strategy
    threads: int
    iterations: int
    violations: int
    violation: (
        str  # what a violation is: "orphan child", "duplicate key", "duplicate id", "lost update"
    )
    seed: int
    duration_ms: int
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.violations == 0


class ConcurrencySkip(FrozenModel):
    """A scenario that could not be probed, and why (never silently missing)."""

    scenario_id: str
    reason: str
