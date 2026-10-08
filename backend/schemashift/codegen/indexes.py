"""Index generation: unique / compound / partial unique indexes and FK-equivalent indexes."""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from schemashift.codegen.layout import TableLayout
from schemashift.codegen.predicate import (
    ColRef,
    PAnd,
    PBool,
    PCmp,
    PNull,
    Pred,
    Untranslatable,
    literal_value,
    parse_predicate,
)
from schemashift.models.base import FrozenModel
from schemashift.models.schema import Schema, Table
from schemashift.semantic.graph import SchemaGraph


class IndexSpec(FrozenModel):
    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False
    partial_filter: dict[str, Any] | None = None
    origin: str = ""  # e.g. "unique:customers.email" or "fk:orders.customer_id->customers.id"
    comment: str = ""


def partial_filter(sql: str, table: Table, layout: TableLayout) -> dict[str, Any] | None:
    """A ``partialFilterExpression`` for a SQL predicate, or None when MongoDB cannot express it.

    Supported: AND of ``col <op> literal`` (eq/gt/gte/lt/lte), boolean columns and IS NOT NULL.
    """
    try:
        pred = parse_predicate(sqlglot.parse_one(sql, dialect="postgres"))
    except Untranslatable:
        return None
    parts = pred.items if isinstance(pred, PAnd) else (pred,)
    out: dict[str, Any] = {}
    for p in parts:
        leaf = _partial_leaf(p, table, layout)
        if leaf is None:
            return None
        for key, value in leaf.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key].update(value)
            else:
                out[key] = value
    return out


def _col(node: exp.Expr, table: Table, layout: TableLayout) -> tuple[str, Any] | None:
    if not isinstance(node, exp.Column):
        return None
    name = str(node.name) if node.this.args.get("quoted") else str(node.name).lower()
    column = table.column(name)
    if column is None or name not in layout.fields:
        return None
    return layout.dotted(name), column


def _partial_leaf(p: Pred, table: Table, layout: TableLayout) -> dict[str, Any] | None:
    if isinstance(p, PCmp) and p.op in ("eq", "gt", "gte", "lt", "lte"):
        found = _col(p.left, table, layout)
        if found is None:
            return None
        path, column = found
        try:
            value = literal_value(p.right, column.sql_type)
        except Untranslatable:
            return None
        if value is None:
            return None
        return {path: value} if p.op == "eq" else {path: {f"${p.op}": value}}
    if isinstance(p, PBool):
        found = _col(p.expr, table, layout)
        return {found[0]: p.value} if found else None
    if isinstance(p, PNull) and p.neg:
        found = _col(p.left, table, layout)
        return {found[0]: {"$exists": True}} if found else None
    return None


def _fields(layout: TableLayout, cols: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(layout.dotted(c) for c in cols)


def build_indexes(
    schema: Schema, graph: SchemaGraph, layouts: dict[str, TableLayout]
) -> list[IndexSpec]:
    specs: list[IndexSpec] = []
    seen: set[tuple[str, tuple[tuple[str, int], ...], bool]] = set()

    def add(spec: IndexSpec) -> None:
        key = (spec.collection, spec.keys, spec.unique)
        if key not in seen:
            seen.add(key)
            specs.append(spec)

    for table in schema.tables.values():
        layout = layouts[table.name]
        _table_indexes(table, layout, add)
    for rel in graph.relationships.values():
        cl = layouts[rel.child]
        if cl.relationship_id == rel.id or cl.kind == "ref_scalars":
            continue  # embedded through this very relationship: nothing to look up
        if not all(cl.has_column(c) for c in rel.fk.columns):
            continue
        fields = _fields(cl, rel.fk.columns)
        if fields == ("_id",):
            continue
        add(
            IndexSpec(
                collection=cl.collection,
                name=_name("idx", cl.collection, fields),
                keys=tuple((f, 1) for f in fields),
                origin=rel.id,
                comment="supports lookups along the foreign key",
            )
        )
    for layout in layouts.values():
        if layout.kind == "ref_scalars":
            add(
                IndexSpec(
                    collection=layout.collection,
                    name=_name("idx", layout.collection, (".".join(layout.path),)),
                    keys=((".".join(layout.path), 1),),
                    origin=f"m2n:{layout.table}",
                    comment="multikey index for reverse lookups of the reference array",
                )
            )
    return specs


def _name(prefix: str, collection: str, fields: tuple[str, ...]) -> str:
    return "_".join([prefix, collection, *(f.replace(".", "_") for f in fields)])[:120]


def _table_indexes(table: Table, layout: TableLayout, add: Any) -> None:
    coll = layout.collection
    if layout.kind == "ref_scalars":
        return
    # composite primary key -> compound unique index (single-column PK is _id)
    pk = table.primary_key
    if pk and len(pk.columns) > 1 and layout.is_root:
        fields = _fields(layout, pk.columns)
        add(
            IndexSpec(
                collection=coll,
                name=_name("pk", coll, fields),
                keys=tuple((f, 1) for f in fields),
                unique=True,
                origin=f"pk:{table.name}.{','.join(pk.columns)}",
                comment="composite primary key",
            )
        )
    unique_sets: list[tuple[str, tuple[str, ...], str | None, str]] = [
        (u.name or "", u.columns, None, "unique") for u in table.uniques
    ] + [(i.name, i.columns, i.where_sql, "unique index") for i in table.indexes if i.unique]
    for name, cols, where, kind in unique_sets:
        _unique_index(table, layout, name, cols, where, kind, add)
    for ix in table.indexes:
        if ix.unique or not all(layout.has_column(c) for c in ix.columns):
            continue
        fields = _fields(layout, ix.columns)
        add(
            IndexSpec(
                collection=coll,
                name=ix.name,
                keys=tuple((f, 1) for f in fields),
                origin=f"index:{table.name}.{ix.name}",
            )
        )


def _unique_index(
    table: Table,
    layout: TableLayout,
    name: str,
    cols: tuple[str, ...],
    where: str | None,
    kind: str,
    add: Any,
) -> None:
    coll = layout.collection
    origin = f"unique:{table.name}.{','.join(cols)}"
    if not all(layout.has_column(c) for c in cols):
        return  # includes the columns implied by nesting: unique per host, not an index
    if layout.dropped_columns and set(layout.dropped_columns) <= set(cols):
        return
    fields = _fields(layout, cols)
    nullable = [c for c in cols if (col := table.column(c)) is not None and col.nullable]
    partial: dict[str, Any] = {}
    comment = f"{kind}"
    if not layout.is_root:
        # parents without embedded rows have no such path; they must not collide as "null"
        partial.update({layout.dotted(c): {"$exists": True} for c in cols})
        comment += "; partial because parents without embedded rows have no such field"
    elif nullable:
        partial.update({layout.dotted(c): {"$exists": True} for c in nullable})
        comment += "; partial so that NULLs (absent fields) do not collide"
    unique = True
    if where is not None:
        extra = partial_filter(where, table, layout)
        if extra is None:
            unique = False
            comment = (
                f"{kind} with a predicate MongoDB cannot express: NOT unique, enforce in the app"
            )
        else:
            for key, value in extra.items():
                if key in partial and isinstance(partial[key], dict) and isinstance(value, dict):
                    partial[key].update(value)
                else:
                    partial[key] = value
            comment += "; partialFilterExpression from the SQL predicate"
    add(
        IndexSpec(
            collection=coll,
            name=name or _name("uq", coll, fields),
            keys=tuple((f, 1) for f in fields),
            unique=unique,
            partial_filter=partial or None,
            origin=origin,
            comment=comment,
        )
    )


def referenced_field(layout: TableLayout, col: str) -> ColRef:
    return ColRef(path=layout.dotted(col))
