"""Request / response DTOs (the OpenAPI contract; the frontend client is generated from these)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from schemashift.codegen.emit import GeneratedFile
from schemashift.equivalence.checker import PlacementChange
from schemashift.models import PlacementDecision, Verdict
from schemashift.models.source import Diagnostic
from schemashift.pipeline import CompileOptions
from schemashift.verification import VerificationReport

MAX_SQL_CHARS = 200_000


class CompileRequest(BaseModel):
    schema_sql: str = Field(max_length=MAX_SQL_CHARS)
    queries_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    seed_sql: str = Field(default="", max_length=MAX_SQL_CHARS)
    options: CompileOptions = CompileOptions()


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


RunStatus = Literal["pending", "running", "done", "failed"]


class RunCreated(BaseModel):
    run_id: str


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
