"""Request / response DTOs (the OpenAPI contract; the frontend client is generated from these)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from schemashift.codegen.emit import GeneratedFile
from schemashift.equivalence.checker import PlacementChange
from schemashift.evolve import EvolutionPlan
from schemashift.models import Counterexample, CounterexampleGap, PlacementDecision, Verdict
from schemashift.models.placement import Placement
from schemashift.models.source import Diagnostic
from schemashift.pipeline import CompileOptions
from schemashift.risk import RiskReport
from schemashift.verification import VerificationReport

MAX_SQL_CHARS = 200_000


class CompileRequest(BaseModel):
    schema_sql: str = Field(max_length=MAX_SQL_CHARS)
    queries_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    seed_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    options: CompileOptions = CompileOptions()


class SimulateRequest(BaseModel):
    """Re-plan with the user's decisions and compare with what the optimizer chose."""

    schema_sql: str = Field(max_length=MAX_SQL_CHARS)
    queries_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    options: CompileOptions = CompileOptions()
    overrides: dict[str, Placement] = {}  # relationship id -> EMBED | REFERENCE
    query_freq: dict[str, float] = {}  # statement id -> relative frequency
    alter_sql: str = Field(default="", max_length=MAX_SQL_CHARS)  # e.g. drop a constraint


class DiffRequest(BaseModel):
    """Two schema versions: the new one as DDL or as an ALTER script applied to the old one."""

    old_sql: str = Field(max_length=MAX_SQL_CHARS)
    new_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    alter_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    queries_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    options: CompileOptions = CompileOptions()
    confirmed_renames: dict[str, str] = {}  # "table" or "table.column" -> new name


class DiffResponse(EvolutionPlan):
    script: str  # the executable incremental migration (Python, pymongo)
    old_diagnostics: list[Diagnostic] = []
    new_diagnostics: list[Diagnostic] = []


class RunRequest(CompileRequest):
    verify: bool = False
    seed: int = 0


class GraphNode(BaseModel):
    id: str
    columns: int
    kind: str  # root | object | array | ref_scalars | ref_docs
    collection: str


class GraphEdge(BaseModel):
    id: str
    child: str
    parent: str
    cardinality: str
    columns: list[str]
    on_delete: str
    decision: str
    self_reference: bool = False


class QueryTranslation(BaseModel):
    query_id: str
    sql: str
    kind: str
    collection: str | None = None
    output_columns: list[str] = []
    error: str | None = None
    notes: list[str] = []
    mongosh: str = ""
    python: str = ""
    transaction_id: str | None = None
    # Phase 19: the pipeline optimizer (empty rewrites = nothing to do)
    rewrites: list[str] = []
    cost_before: float | None = None
    cost_after: float | None = None
    index_hints: list[str] = []
    original_mongosh: str = ""


class UntranslatedCheckDTO(BaseModel):
    table: str
    name: str | None
    sql: str
    fragment: str


class CompileResponse(BaseModel):
    overall_verdict: Literal["SAFE", "CHANGED", "BROKEN"]
    counts: dict[str, int]
    has_errors: bool
    diagnostics: list[Diagnostic]
    queries_line_offset: int
    ir_text: str
    ir_node_count: int
    verdicts: list[Verdict]
    concurrency_verdicts: list[
        Verdict
    ] = []  # concurrency scenarios (kept apart from the guarantees)
    concurrency_counts: dict[str, int] = {}
    concurrency_overall: Literal["SAFE", "CHANGED", "BROKEN"] = "SAFE"
    initial_counts: dict[str, int]
    changed_by_placement: list[PlacementChange]
    plan: list[PlacementDecision]
    plan_warnings: list[str]
    graph_nodes: list[GraphNode]
    graph_edges: list[GraphEdge]
    ai_enabled: bool
    generated: list[GeneratedFile]
    queries: list[QueryTranslation]
    untranslated_checks: list[UntranslatedCheckDTO]
    warnings: list[str]
    risk: RiskReport | None = None  # transparent rule-based migration risk (Phase 24)


RunStatus = Literal["pending", "running", "done", "failed", "cancelled"]


class RunCreated(BaseModel):
    run_id: str


class RunCancelled(BaseModel):
    run_id: str
    status: RunStatus


class RunSummary(BaseModel):
    id: str
    created_at: datetime
    status: RunStatus
    overall_verdict: str | None
    counts: dict[str, int] | None
    verify: bool
    duration_ms: int | None
    schema_preview: str


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    page: int
    page_size: int


class RunDetail(BaseModel):
    id: str
    created_at: datetime
    status: RunStatus
    verify: bool
    schema_sql: str
    queries_sql: str
    seed_sql: str
    options: dict[str, Any]
    seed: int
    overall_verdict: str | None
    counts: dict[str, int] | None
    duration_ms: int | None
    error: str | None
    result: CompileResponse | None
    verification: VerificationReport | None


class CounterexampleReplay(BaseModel):
    verdict_id: str
    counterexample: Counterexample | None = None
    gap: CounterexampleGap | None = None  # why there is no counterexample


class MetricsResponse(BaseModel):
    available: bool
    data: dict[str, Any] | None = None


class SampleInfo(BaseModel):
    name: str
    description: str = ""


class SampleDetail(SampleInfo):
    schema_sql: str
    queries_sql: str
    options: CompileOptions
