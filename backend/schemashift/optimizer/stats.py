"""Statistics measured from seed data (INSERT ... VALUES): fan-out per relationship and row sizes.

When seed data is supplied the optimizer uses what it measures instead of defaults: the average and
maximum number of children per parent (the maximum drives the unbounded-growth and document-size
constraints, the skew between them is a risk signal of its own) and the average size of a child row.
Pure: it only reads parsed statements.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from sqlglot import exp

from schemashift.models.query import Query, TransactionBlock
from schemashift.optimizer.workload import flatten, write_target
from schemashift.semantic.graph import SchemaGraph


@dataclass(frozen=True)
class RowSample:
    """The rows one table received from the seed INSERTs: column -> cell text (None = NULL)."""

    columns: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]
    value_bytes: float  # average bytes of the cell values of one row


@dataclass(frozen=True)
class FanoutStats:
    child_rows: int
    parent_rows: int
    avg: float  # children per parent row (parents without children count as zero)
    max: int  # most children any single parent has
    p95: int
    child_row_value_bytes: (
        float | None
    )  # average bytes of one child row's values (None: no child rows)

    @property
    def skew(self) -> float:
        """(max - avg) / avg: 0 when every parent has the average number of children."""
        return (self.max - self.avg) / self.avg if self.avg > 0 else 0.0


def _cell_bytes(node: exp.Expr) -> int:
    if isinstance(node, exp.Null):
        return 0
    if isinstance(node, exp.Boolean):
        return 1
    if isinstance(node, exp.Literal) and node.is_string:
        return len(str(node.this)) + 5  # BSON string: length prefix + terminator
    if isinstance(node, exp.Literal):
        return 8
    return 16


def _cell_text(node: exp.Expr) -> str | None:
    return None if isinstance(node, exp.Null) else str(node.sql(dialect="postgres"))


def sample_rows(
    graph: SchemaGraph, seed_queries: Iterable[Query | TransactionBlock]
) -> dict[str, RowSample]:
    """Rows per table from INSERT ... VALUES statements (other statement kinds are ignored)."""
    cols: dict[str, tuple[str, ...]] = {}
    rows: dict[str, list[tuple[str | None, ...]]] = {}
    sizes: dict[str, int] = {}
    for q in flatten(seed_queries):
        if q.kind != "INSERT":
            continue
        table = write_target(q)
        values = q.ast.args.get("expression")
        if not table or table not in graph.schema.tables or not isinstance(values, exp.Values):
            continue
        target = q.ast.this
        names = (
            tuple(str(i.name).lower() for i in target.expressions if isinstance(i, exp.Identifier))
            if isinstance(target, exp.Schema) and target.expressions
            else tuple(c.name for c in graph.schema.tables[table].columns)
        )
        cols.setdefault(table, names)
        if cols[table] != names:
            continue  # a different column list for the same table: ignore the odd statement
        for tup in values.expressions:
            items = tup.expressions if isinstance(tup, exp.Tuple) else [tup]
            if len(items) != len(names):
                continue
            rows.setdefault(table, []).append(tuple(_cell_text(i) for i in items))
            sizes[table] = sizes.get(table, 0) + sum(_cell_bytes(i) for i in items)
    return {t: RowSample(cols[t], tuple(r), sizes[t] / len(r)) for t, r in rows.items() if r}


def fanout_statistics(
    graph: SchemaGraph, seed_queries: Iterable[Query | TransactionBlock]
) -> dict[str, FanoutStats]:
    """Per relationship id: how many children each parent has in the seed data."""
    samples = sample_rows(graph, seed_queries)
    out: dict[str, FanoutStats] = {}
    for rel in graph.relationships.values():
        child, parent = samples.get(rel.child), samples.get(rel.parent)
        if child is None or parent is None:
            continue
        try:
            idx = [child.columns.index(c) for c in rel.fk.columns]
        except ValueError:
            continue
        per_parent: Counter[tuple[str | None, ...]] = Counter(
            tuple(row[i] for i in idx) for row in child.rows if all(row[i] is not None for i in idx)
        )
        counts = sorted(per_parent.values())
        total = sum(counts)
        if not counts or total == 0:
            continue
        p95 = counts[min(len(counts) - 1, math.ceil(0.95 * len(counts)) - 1)]
        out[rel.id] = FanoutStats(
            child_rows=len(child.rows),
            parent_rows=len(parent.rows),
            avg=total / len(parent.rows),
            max=counts[-1],
            p95=p95,
            child_row_value_bytes=child.value_bytes,
        )
    return out
