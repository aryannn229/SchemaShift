"""Access-pattern hints derived from supplied queries and seed data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlglot import exp

from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.semantic.graph import Relationship, SchemaGraph

WriteFrequency = Literal["low", "med", "high"]


class AccessHint(FrozenModel):
    """User supplied overrides for one relationship (all optional)."""

    child_independent_access: float | None = None
    read_together_ratio: float | None = None
    child_write_frequency: WriteFrequency | None = None
    estimated_child_count_per_parent: int | None = None


@dataclass(frozen=True)
class WorkloadStats:
    child_independent_access: float | None = None
    read_together_ratio: float | None = None
    child_write_frequency: WriteFrequency | None = None


def flatten(queries: Iterable[Query | TransactionBlock]) -> list[Query]:
    flat: list[Query] = []
    for q in queries:
        if isinstance(q, TransactionBlock):
            flat.extend(q.statements)
        else:
            flat.append(q)
    return flat


def _table_name(node: exp.Expr | None) -> str | None:
    if isinstance(node, exp.Schema):
        node = node.this
    if isinstance(node, exp.Table):
        ident = node.this
        name = str(node.name)
        return name if ident is not None and ident.args.get("quoted") else name.lower()
    return None


def tables_in(q: Query) -> set[str]:
    names = {_table_name(t) for t in q.ast.find_all(exp.Table)}
    return {n for n in names if n}


def write_target(q: Query) -> str | None:
    """The table a write statement modifies."""
    if q.kind == "SELECT":
        return None
    return _table_name(q.ast.this)


def analyze_workload(
    graph: SchemaGraph, queries: Iterable[Query | TransactionBlock]
) -> dict[str, WorkloadStats]:
    flat = flatten(queries)
    selects = [(q, tables_in(q)) for q in flat if q.kind == "SELECT"]
    writes = [t for t in (write_target(q) for q in flat) if t]
    stats: dict[str, WorkloadStats] = {}
    for rel in graph.relationships.values():
        stats[rel.id] = _stats_for(rel, selects, writes, bool(flat))
    return stats


def _stats_for(
    rel: Relationship,
    selects: list[tuple[Query, set[str]]],
    writes: list[str],
    have_queries: bool,
) -> WorkloadStats:
    touching_child = [t for _, t in selects if rel.child in t]
    independent = (
        sum(1 for t in touching_child if rel.parent not in t) / len(touching_child)
        if touching_child
        else None
    )
    together = (
        sum(1 for t in touching_child if rel.parent in t) / len(touching_child)
        if touching_child
        else None
    )
    freq: WriteFrequency | None = None
    if have_queries:
        n = sum(1 for w in writes if w == rel.child)
        freq = "low" if n == 0 else ("med" if n <= 2 else "high")
    return WorkloadStats(
        child_independent_access=independent,
        read_together_ratio=together,
        child_write_frequency=freq,
    )


def seed_row_counts(queries: Iterable[Query | TransactionBlock]) -> dict[str, int]:
    """Rows inserted per table by INSERT ... VALUES statements (used as fan-out estimates)."""
    counts: dict[str, int] = {}
    for q in flatten(queries):
        if q.kind != "INSERT":
            continue
        table = write_target(q)
        values = q.ast.args.get("expression")
        if table and isinstance(values, exp.Values):
            counts[table] = counts.get(table, 0) + len(values.expressions)
    return counts
