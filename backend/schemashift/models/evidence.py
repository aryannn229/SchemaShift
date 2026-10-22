"""Evidence attached to a verdict: how sure we are, under which conditions, and a concrete example.

``SAFE`` / ``CHANGED`` / ``BROKEN`` say *what* happens to a guarantee; the evidence level says *how
we know*:

* ``PROVEN``      a sound argument (stated in ``argument``) established it; no run is needed.
* ``CONDITIONAL`` it holds only under the stated, structured ``conditions`` (for example "every
                  write goes through the generated helper").
* ``TESTED``      supported by probes, differential or fuzz runs on real databases, not proven.
* ``UNVERIFIED``  no evidence either way (the default until a run supplies some).
"""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel

EvidenceLevel = Literal["PROVEN", "CONDITIONAL", "TESTED", "UNVERIFIED"]
EVIDENCE_ORDER: tuple[EvidenceLevel, ...] = ("PROVEN", "CONDITIONAL", "TESTED", "UNVERIFIED")

ParamValue = str | int | float | bool


class EvidenceCondition(FrozenModel):
    """One condition under which a verdict holds, as data (the text is for humans)."""

    kind: str  # validators_installed | app_enforcement | immutable_keys | bounded_array | ...
    text: str
    parameters: dict[str, ParamValue] = {}


class RuleInput(FrozenModel):
    """A value the rule consulted ("Why?" trace): placement decision, option or node."""

    name: str
    value: str


class CounterexampleStep(FrozenModel):
    """One operation of a counterexample, with what each database did."""

    description: str
    sql: str | None = None  # the PostgreSQL statement, bound parameters inlined (display only)
    postgres: str
    mongo: str


class Counterexample(FrozenModel):
    """The smallest data and operation sequence found that shows the behaviours differ."""

    verdict_id: str
    description: str
    setup: tuple[str, ...]  # INSERT statements that build the minimal dataset
    steps: tuple[CounterexampleStep, ...]
    difference: str  # e.g. "orders: PostgreSQL 0 row(s) vs MongoDB 3"
    tables_differing: tuple[str, ...] = ()
    rows_in_setup: int = 0
    minimal: bool = True  # False when the data could not be reduced further automatically
    reproducible: bool = True  # False when only a generic (not minimal) demonstration exists
    seed: int = 0


class EvidenceUpgrade(FrozenModel):
    """A verification run raised an UNVERIFIED verdict to TESTED, and on what basis."""

    verdict_id: str
    level: EvidenceLevel
    basis: str


class CounterexampleGap(FrozenModel):
    """Why no counterexample exists for a BROKEN / CHANGED verdict (never silently missing)."""

    verdict_id: str
    reason: str
