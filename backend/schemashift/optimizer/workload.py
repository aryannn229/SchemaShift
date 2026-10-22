"""Access-pattern hints derived from supplied queries and seed data."""

from __future__ import annotations

import re
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


class WorkloadSpec(FrozenModel):
    """Per-query relative frequencies (a sidecar ``workload.yaml`` or the API option).

    Keys are query ids (``q1``, ``q2``, ... in file order). A ``-- @freq N`` comment before a
    statement
    sets the same thing inline; an explicit entry here wins over a comment. Unlisted queries weigh
    ``default_freq`` (1).
    """

    query_freq: dict[str, float] = {}
    default_freq: float = 1.0


FREQ_RE = re.compile(r"--\s*@freq\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


def annotation_frequencies(
    queries_sql: str, queries: Iterable[Query | TransactionBlock], line_offset: int = 0
) -> dict[str, float]:
    """Frequencies from ``-- @freq N`` comments: each applies to the next statement after it.

    ``line_offset`` converts statement spans (which count lines of schema + queries) to lines of
    ``queries_sql``. A comment before ``BEGIN`` applies to every statement of the transaction.
    """
    marks = [
        (i + 1, float(m.group(1)))
        for i, line in enumerate(queries_sql.splitlines())
        if (m := FREQ_RE.search(line))
    ]
    if not marks:
        return {}
    units: list[tuple[int, list[str]]] = []
    for item in queries:
        if isinstance(item, TransactionBlock):
            units.append(
                (
                    item.span.line_start - line_offset if item.span else 0,
                    [s.id for s in item.statements],
                )
            )
        else:
            units.append((item.span.line_start - line_offset if item.span else 0, [item.id]))
    units.sort(key=lambda u: u[0])
    out: dict[str, float] = {}
    previous = 0
    for start, ids in units:
        before = [value for line, value in marks if previous < line < start]
        if before:
            for qid in ids:
                out[qid] = before[-1]
        previous = start
    return out


def merge_frequencies(annotated: dict[str, float], spec: WorkloadSpec | None) -> dict[str, float]:
    """Explicit sidecar/API frequencies win over comments."""
    return {**annotated, **(spec.query_freq if spec else {})}


@dataclass(frozen=True)
class WorkloadStats:
    child_independent_access: float | None = None
    read_together_ratio: float | None = None
    child_write_frequency: WriteFrequency | None = None
    child_write_share: float | None = (
        None  # frequency-weighted share of statements writing the child
    )
    parent_write_share: float | None = None
    weighted: bool = False  # the workload carries information beyond plain query counts


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
    graph: SchemaGraph,
    queries: Iterable[Query | TransactionBlock],
    freq: dict[str, float] | None = None,
    default_freq: float = 1.0,
) -> dict[str, WorkloadStats]:
    """Frequency-weighted access statistics per relationship (weights default to 1 per
    statement)."""
    flat = flatten(queries)
    freq = freq or {}

    def weight(q: Query) -> float:
        return max(0.0, freq.get(q.id, default_freq))

    selects = [(q, tables_in(q), weight(q)) for q in flat if q.kind == "SELECT"]
    writes = [(t, weight(q)) for q in flat if (t := write_target(q))]
    total = sum(weight(q) for q in flat)
    weighted = any(abs(weight(q) - 1.0) > 1e-9 for q in flat) or bool(writes)
    stats: dict[str, WorkloadStats] = {}
    for rel in graph.relationships.values():
        stats[rel.id] = _stats_for(rel, selects, writes, total, weighted, bool(flat))
    return stats


def _stats_for(
    rel: Relationship,
    selects: list[tuple[Query, set[str], float]],
    writes: list[tuple[str, float]],
    total: float,
    weighted: bool,
    have_queries: bool,
) -> WorkloadStats:
    touching_child = [(t, w) for _, t, w in selects if rel.child in t]
    reads_weight = sum(w for _, w in touching_child)
    independent = (
        sum(w for t, w in touching_child if rel.parent not in t) / reads_weight
        if reads_weight
        else None
    )
    together = (
        sum(w for t, w in touching_child if rel.parent in t) / reads_weight
        if reads_weight
        else None
    )
    freq: WriteFrequency | None = None
    child_share: float | None = None
    parent_share: float | None = None
    if have_queries:
        n = sum(w for t, w in writes if t == rel.child)
        freq = "low" if n == 0 else ("med" if n <= 2 else "high")
        if total > 0:
            child_share = n / total
            parent_share = sum(w for t, w in writes if t == rel.parent) / total
    return WorkloadStats(
        child_independent_access=independent,
        read_together_ratio=together,
        child_write_frequency=freq,
        child_write_share=child_share,
        parent_write_share=parent_share,
        weighted=weighted,
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
