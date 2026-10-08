"""Semantic analysis: relationship graph, cardinality inference and validators."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Schema
from schemashift.models.source import Diagnostic
from schemashift.semantic.cardinality import Cardinality, JunctionTable
from schemashift.semantic.graph import Relationship, SchemaGraph, build_graph, fk_id
from schemashift.semantic.validators import (
    SCHEMA_CHECKS,
    check_circular_fk,
    check_queries,
    check_self_reference,
)


@dataclass
class AnalysisResult:
    graph: SchemaGraph
    diagnostics: tuple[Diagnostic, ...]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)


def _ordered(diags: list[Diagnostic]) -> list[Diagnostic]:
    def key(d: Diagnostic) -> tuple[int, int, str]:
        loc = d.location
        return (loc.line_start, loc.col_start, d.code) if loc else (10**9, 0, d.code)

    return sorted(diags, key=key)


def analyze(schema: Schema, queries: Iterable[Query | TransactionBlock] = ()) -> AnalysisResult:
    """Build the schema graph and run every validator."""
    graph = build_graph(schema)
    diags: list[Diagnostic] = []
    for check in SCHEMA_CHECKS:
        diags.extend(check(schema))
    diags.extend(check_circular_fk(graph))
    diags.extend(check_self_reference(graph))
    diags.extend(check_queries(schema, queries))
    return AnalysisResult(graph=graph, diagnostics=tuple(_ordered(diags)))


__all__ = [
    "AnalysisResult",
    "Cardinality",
    "JunctionTable",
    "Relationship",
    "SchemaGraph",
    "analyze",
    "build_graph",
    "fk_id",
]
