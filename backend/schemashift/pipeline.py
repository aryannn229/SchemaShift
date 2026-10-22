"""Orchestrates the whole compile: parse, semantic, IR, equivalence, optimizer, final pass."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from schemashift.ai import AdvisorCache, LLMAdvisor, attach_opinions
from schemashift.equivalence import (
    ConcurrencyReport,
    EquivalenceResult,
    RuleOptions,
    check_both,
    check_concurrency,
)
from schemashift.ir import IRProgram, build_ir, print_ir
from schemashift.models import Placement, PlacementPlan
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Schema
from schemashift.models.source import Diagnostic
from schemashift.optimizer import AccessHint, OptimizerOptions, extract_features, plan_placement
from schemashift.optimizer.sensitivity import analyze_sensitivity, attach_fragility
from schemashift.optimizer.workload import (
    WorkloadSpec,
    annotation_frequencies,
    merge_frequencies,
)
from schemashift.parser import parse
from schemashift.semantic import SchemaGraph, analyze


class CompileOptions(FrozenModel):
    target_mongo_version: str = "7.0"
    transactions_available: bool = True
    preserve_integer_ids: bool = False
    uuid_as: Literal["binary", "string"] = "binary"
    # How MongoDB compares strings. "en" gives the collections an ICU English collation, which
    # matches PostgreSQL databases created with an en_US-style locale (the common default); use
    # "binary" when the PostgreSQL database uses the C / POSIX collation (byte order).
    string_collation: Literal["en", "binary"] = "en"
    rows_per_table: int = 50
    relationship_overrides: dict[str, Placement] = {}
    access_hints: dict[str, AccessHint] = {}
    # Phase 18: per-query frequencies (sidecar workload.yaml / API) and the weight sensitivity
    # analysis
    workload: WorkloadSpec = WorkloadSpec()
    sensitivity: bool = True

    def rule_options(self) -> RuleOptions:
        return RuleOptions(
            target_mongo_version=self.target_mongo_version,
            transactions_available=self.transactions_available,
            preserve_integer_ids=self.preserve_integer_ids,
            uuid_as=self.uuid_as,
        )

    def optimizer_options(self, query_freq: dict[str, float] | None = None) -> OptimizerOptions:
        return OptimizerOptions(
            access_hints=self.access_hints,
            relationship_overrides=self.relationship_overrides,
            query_freq=query_freq if query_freq is not None else dict(self.workload.query_freq),
            default_freq=self.workload.default_freq,
        )


class CompileResult(FrozenModel):
    schema_: Schema
    queries: tuple[Query | TransactionBlock, ...]
    diagnostics: tuple[Diagnostic, ...]
    ir: IRProgram
    ir_text: str
    plan: PlacementPlan
    equivalence: EquivalenceResult
    concurrency: ConcurrencyReport = ConcurrencyReport()
    queries_line_offset: int = 0  # spans refer to schema_sql + "\n" + queries_sql
    graph: SchemaGraph = Field(exclude=True)
    seed_queries: tuple[Query | TransactionBlock, ...] = Field(default=(), exclude=True)

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    @property
    def overall_verdict(self) -> str:
        return self.equivalence.final.overall


def compile_sql(
    schema_sql: str,
    queries_sql: str = "",
    seed_sql: str = "",
    options: CompileOptions | None = None,
    advisor: LLMAdvisor | None = None,
    cache: AdvisorCache | None = None,
) -> CompileResult:
    """Run every compiler stage. Never executes the SQL. The advisor (if any) is advisory only."""
    options = options or CompileOptions()
    parsed = parse(schema_sql + "\n" + queries_sql)
    seed = parse(seed_sql, "queries") if seed_sql.strip() else None
    analysis = analyze(parsed.schema_, parsed.queries)
    program = build_ir(analysis.graph, parsed.queries)
    seed_queries = seed.queries if seed else ()
    offset = schema_sql.count("\n") + 1
    freq = merge_frequencies(
        annotation_frequencies(queries_sql, parsed.queries, offset), options.workload
    )
    opt_options = options.optimizer_options(freq)
    plan = plan_placement(analysis.graph, program, parsed.queries, seed_queries, opt_options)
    features = None
    if advisor is not None or (options.sensitivity and analysis.graph.relationships):
        features = extract_features(
            analysis.graph,
            program,
            parsed.queries,
            seed_queries,
            options.access_hints,
            None,
            freq,
            options.workload.default_freq,
        )
    if options.sensitivity and features:
        from schemashift.optimizer.weights import load_weights

        report = analyze_sensitivity(analysis.graph, features, opt_options, load_weights(), plan)
        plan = attach_fragility(plan, report)
    if advisor is not None and features is not None:
        plan = attach_opinions(plan, features, parsed.schema_, advisor, cache)
    equivalence = check_both(program, analysis.graph, plan, options.rule_options())
    concurrency = check_concurrency(program, analysis.graph, plan, options.rule_options())
    diagnostics = [*parsed.diagnostics, *analysis.diagnostics]
    if seed:
        diagnostics.extend(seed.diagnostics)
    return CompileResult(
        schema_=parsed.schema_,
        queries=parsed.queries,
        diagnostics=tuple(diagnostics),
        ir=program,
        ir_text=print_ir(program),
        plan=plan,
        equivalence=equivalence,
        concurrency=concurrency,
        queries_line_offset=schema_sql.count("\n") + 1,
        graph=analysis.graph,
        seed_queries=seed_queries,
    )
