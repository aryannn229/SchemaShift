"""Result types of the SMT check."""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel

Direction = Literal["mongo_accepts_sql_rejects", "sql_accepts_mongo_rejects"]
TableStatus = Literal["EQUIVALENT", "COUNTEREXAMPLE", "UNSUPPORTED", "TIMEOUT"]


class SmtFinding(FrozenModel):
    """A row on which the two validators disagree."""

    direction: Direction
    row: dict[str, str | None]  # column -> value, None = SQL NULL (field absent in MongoDB)
    explanation: str
    nodes: tuple[str, ...]  # the verdict node ids the disagreement is about
    clause: str | None = None  # the MongoDB validator clause involved


class TableResult(FrozenModel):
    table: str
    collection: str
    status: TableStatus
    reason: str = ""
    encoded: tuple[str, ...] = ()  # verdict node ids covered by the proof
    findings: tuple[SmtFinding, ...] = ()
    milliseconds: int = 0


class SmtReport(FrozenModel):
    solver: str
    timeout_ms: int
    tables: tuple[TableResult, ...]

    @property
    def proven_nodes(self) -> set[str]:
        return {n for t in self.tables if t.status == "EQUIVALENT" for n in t.encoded}
