"""Typed results of the incremental migration compiler (Phase 22)."""

from __future__ import annotations

from typing import Any, Literal

from schemashift.models.base import FrozenModel
from schemashift.models.placement import Placement

ChangeKind = Literal[
    "ADD_TABLE",
    "DROP_TABLE",
    "RENAME_TABLE",
    "ADD_COLUMN",
    "DROP_COLUMN",
    "RENAME_COLUMN",
    "RETYPE_COLUMN",
    "CHANGE_PRIMARY_KEY",
    "ADD_FOREIGN_KEY",
    "DROP_FOREIGN_KEY",
    "ADD_UNIQUE",
    "DROP_UNIQUE",
    "ADD_CHECK",
    "DROP_CHECK",
    "SET_NOT_NULL",
    "DROP_NOT_NULL",
    "SET_DEFAULT",
    "DROP_DEFAULT",
    "ADD_INDEX",
    "DROP_INDEX",
]


class ChangeOp(FrozenModel):
    """One typed difference between two schema versions."""

    kind: ChangeKind
    table: str
    column: str | None = None
    detail: dict[str, Any] = {}
    description: str


class RenameCandidate(FrozenModel):
    """A drop + add that looks like a rename. Never applied without confirmation."""

    kind: Literal["table", "column"]
    table: str  # the table (old name) for columns, the old name for tables
    old: str
    new: str
    score: float
    confirmed: bool = False

    @property
    def key(self) -> str:
        return f"{self.table}.{self.old}" if self.kind == "column" else self.old


class SchemaDiff(FrozenModel):
    ops: tuple[ChangeOp, ...]
    rename_candidates: tuple[RenameCandidate, ...] = ()
    warnings: tuple[str, ...] = ()


class PlanChange(FrozenModel):
    """A relationship whose placement differs between the two versions."""

    relationship_id: str
    old: Placement | None
    new: Placement | None
    restructures: bool
    reason: str


Reversibility = Literal["REVERSIBLE", "REVERSIBLE_WITH_SAVED_DATA", "IRREVERSIBLE"]
StepKind = Literal[
    "CREATE_COLLECTION",
    "ARCHIVE_COLLECTION",
    "UPDATE_VALIDATOR",
    "CREATE_INDEX",
    "DROP_INDEX",
    "ADD_FIELD",
    "UNSET_FIELD",
    "RENAME_FIELD",
    "RETYPE_FIELD",
    "EXTRACT_EMBEDDED",
    "EMBED_COLLECTION",
    "MANUAL",
]


class MigrationStep(FrozenModel):
    id: str
    kind: StepKind
    collection: str | None
    description: str
    depends_on: tuple[str, ...] = ()
    reversibility: Reversibility
    reason: str  # why it has that reversibility
    code: str = ""  # executable pymongo statements; ``db`` is the target database
    rollback: str | None = None  # present only for REVERSIBLE and REVERSIBLE_WITH_SAVED_DATA steps
    saved_data: str | None = None  # where the data needed to roll back is kept
    recovery: str | None = None  # IRREVERSIBLE: what to do instead (snapshot first)
    manual: bool = False  # SchemaShift cannot automate this step
    changes: tuple[str, ...] = ()  # the ChangeOp descriptions it implements


class VerdictChange(FrozenModel):
    """A guarantee verdict that differs between the old and the new version."""

    node_id: str
    rule_id: str
    old: str | None  # SAFE / CHANGED / BROKEN, None when the guarantee did not exist
    new: str | None
    direction: Literal["worse", "better", "added", "removed", "same"]
    because: tuple[str, ...]  # the schema changes that explain it
    reason: str


class GuaranteeDiff(FrozenModel):
    old_overall: str
    new_overall: str
    changes: tuple[VerdictChange, ...]
    regressions: int
    improvements: int


class EvolutionPlan(FrozenModel):
    diff: SchemaDiff
    plan_changes: tuple[PlanChange, ...]
    steps: tuple[MigrationStep, ...]
    guarantees: GuaranteeDiff
    warnings: tuple[str, ...] = ()
