"""Orchestrates the whole compile: parse, semantic, IR, equivalence, optimizer, final pass."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from schemashift.equivalence import EquivalenceResult, RuleOptions, check_both
from schemashift.ir import IRProgram, build_ir, print_ir
from schemashift.models import Placement, PlacementPlan
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Schema
from schemashift.models.source import Diagnostic
from schemashift.optimizer import AccessHint, OptimizerOptions, plan_placement
from schemashift.parser import parse
from schemashift.semantic import SchemaGraph, analyze


class CompileOptions(FrozenModel):
    target_mongo_version: str = "7.0"
    transactions_available: bool = True
    preserve_integer_ids: bool = False
    uuid_as: Literal["binary", "string"] = "binary"
    rows_per_table: int = 50
    relationship_overrides: dict[str, Placement] = {}
    access_hints: dict[str, AccessHint] = {}

    def rule_options(self) -> RuleOptions:
        return RuleOptions(
            target_mongo_version=self.target_mongo_version,
            transactions_available=self.transactions_available,
            preserve_integer_ids=self.preserve_integer_ids,
            uuid_as=self.uuid_as,
        )

    def optimizer_options(self) -> OptimizerOptions:
        return OptimizerOptions(
            access_hints=self.access_hints, relationship_overrides=self.relationship_overrides
        )


class CompileResult(FrozenModel):
    schema_: Schema
    queries: tuple[Query | TransactionBlock, ...]
    diagnostics: tuple[Diagnostic, ...]
    ir: IRProgram
    ir_text: str
    plan: PlacementPlan
    equivalence: EquivalenceResult
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
) -> CompileResult:
    """Run every pure compiler stage. Never executes the SQL."""
    options = options or CompileOptions()
    parsed = parse(schema_sql + "\n" + queries_sql)
    seed = parse(seed_sql, "queries") if seed_sql.strip() else None
    analysis = analyze(parsed.schema_, parsed.queries)
    program = build_ir(analysis.graph, parsed.queries)
    seed_queries = seed.queries if seed else ()
    plan = plan_placement(
        analysis.graph, program, parsed.queries, seed_queries, options.optimizer_options()
    )
    equivalence = check_both(program, analysis.graph, plan, options.rule_options())
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
        queries_line_offset=schema_sql.count("\n") + 1,
        graph=analysis.graph,
        seed_queries=seed_queries,
    )
