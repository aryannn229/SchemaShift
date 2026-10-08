"""Semantic validators. Each check is its own function with its own diagnostic code."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import networkx as nx
from sqlglot import exp

from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import NormalizedType, Schema, Table
from schemashift.models.source import Diagnostic, SourceSpan
from schemashift.semantic.cardinality import unique_column_sets
from schemashift.semantic.diagnostics import (
    SEM001,
    SEM002,
    SEM003,
    SEM004,
    SEM005,
    SEM006,
    SEM007,
    SEM008,
    SEM009,
    SEM010,
    SEM011,
    SEM012,
    diag,
)
from schemashift.semantic.graph import SchemaGraph, fk_id

SchemaCheck = Callable[[Schema], list[Diagnostic]]

_INT_RANK = {"SMALLINT": 1, "INTEGER": 2, "BIGINT": 3}
_TEXTY = {"TEXT", "VARCHAR", "CHAR"}


# --- SEM001 -----------------------------------------------------------------------------
def check_fk_missing_table(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            if fk.ref_table not in schema.tables:
                out.append(
                    diag(
                        "error",
                        SEM001,
                        f"Foreign key on '{table.name}({', '.join(fk.columns)})' references "
                        f"table '{fk.ref_table}', which does not exist",
                        fk.span,
                    )
                )
    return out


# --- SEM002 -----------------------------------------------------------------------------
def check_fk_missing_columns(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            for col in fk.columns:
                if table.column(col) is None:
                    out.append(
                        diag(
                            "error",
                            SEM002,
                            f"Foreign key column '{table.name}.{col}' does not exist",
                            fk.span,
                        )
                    )
            parent = schema.tables.get(fk.ref_table)
            if parent is None:
                continue
            for col in fk.ref_columns:
                if parent.column(col) is None:
                    out.append(
                        diag(
                            "error",
                            SEM002,
                            f"Foreign key on '{table.name}' references column "
                            f"'{parent.name}.{col}', which does not exist",
                            fk.span,
                        )
                    )
    return out


# --- SEM003 -----------------------------------------------------------------------------
def check_fk_column_count(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            if fk.ref_columns and len(fk.columns) != len(fk.ref_columns):
                out.append(
                    diag(
                        "error",
                        SEM003,
                        f"Foreign key on '{table.name}' has {len(fk.columns)} column(s) but "
                        f"references {len(fk.ref_columns)} column(s) of '{fk.ref_table}'",
                        fk.span,
                    )
                )
    return out


# --- SEM004 -----------------------------------------------------------------------------
def types_compatibility(a: NormalizedType, b: NormalizedType) -> str:
    """Return ``"ok"``, ``"widening"`` or ``"mismatch"``."""
    if a.is_array != b.is_array:
        return "mismatch"
    if a.base == b.base:
        if a.base == "ENUM" and a.enum_name != b.enum_name:
            return "mismatch"
        return "ok"
    if a.base in _TEXTY and b.base in _TEXTY:
        return "ok"
    if a.base in _INT_RANK and b.base in _INT_RANK:
        return "widening"
    return "mismatch"


def check_fk_type_mismatch(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            parent = schema.tables.get(fk.ref_table)
            if parent is None or len(fk.columns) != len(fk.ref_columns):
                continue
            for child_col, parent_col in zip(fk.columns, fk.ref_columns, strict=True):
                c = table.column(child_col)
                p = parent.column(parent_col)
                if c is None or p is None:
                    continue
                verdict = types_compatibility(c.sql_type, p.sql_type)
                if verdict == "ok":
                    continue
                desc = (
                    f"'{table.name}.{child_col}' ({c.sql_type.base}) vs "
                    f"'{parent.name}.{parent_col}' ({p.sql_type.base})"
                )
                if verdict == "widening":
                    out.append(
                        diag("warning", SEM004, f"Foreign key integer widening: {desc}", fk.span)
                    )
                else:
                    out.append(diag("error", SEM004, f"Foreign key type mismatch: {desc}", fk.span))
    return out


# --- SEM005 -----------------------------------------------------------------------------
def check_fk_target_is_key(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            parent = schema.tables.get(fk.ref_table)
            if parent is None:
                continue
            if not fk.ref_columns:
                out.append(
                    diag(
                        "error",
                        SEM005,
                        f"Foreign key on '{table.name}' references '{parent.name}', which has "
                        "no primary key to target implicitly",
                        fk.span,
                    )
                )
                continue
            if any(parent.column(c) is None for c in fk.ref_columns):
                continue  # reported as SEM002
            if frozenset(fk.ref_columns) not in unique_column_sets(parent):
                out.append(
                    diag(
                        "error",
                        SEM005,
                        f"Foreign key on '{table.name}' references "
                        f"'{parent.name}({', '.join(fk.ref_columns)})', which is not a primary "
                        "key or unique",
                        fk.span,
                    )
                )
    return out


# --- SEM006 -----------------------------------------------------------------------------
# Duplicate *tables* are detected by the parser (a dict cannot hold them); see DECISIONS.md.
def check_duplicate_columns(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        seen: set[str] = set()
        for col in table.columns:
            if col.name in seen:
                out.append(
                    diag(
                        "error",
                        SEM006,
                        f"Duplicate column '{col.name}' in table '{table.name}'",
                        col.span,
                    )
                )
            seen.add(col.name)
    return out


# --- SEM007 -----------------------------------------------------------------------------
def _unknown(table: Table, cols: Iterable[str]) -> list[str]:
    return [c for c in cols if table.column(c) is None]


def check_key_unknown_columns(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        groups: list[tuple[str, tuple[str, ...], SourceSpan | None]] = []
        if table.primary_key is not None:
            groups.append(("PRIMARY KEY", table.primary_key.columns, table.primary_key.span))
        groups.extend(("UNIQUE", u.columns, u.span) for u in table.uniques)
        groups.extend((f"INDEX {i.name}", i.columns, i.span) for i in table.indexes)
        for kind, cols, span in groups:
            for bad in _unknown(table, cols):
                out.append(
                    diag(
                        "error",
                        SEM007,
                        f"{kind} on '{table.name}' references unknown column '{bad}'",
                        span,
                    )
                )
    return out


# --- SEM008 -----------------------------------------------------------------------------
def check_no_primary_key(schema: Schema) -> list[Diagnostic]:
    return [
        diag("warning", SEM008, f"Table '{t.name}' has no primary key", t.span)
        for t in schema.tables.values()
        if t.primary_key is None
    ]


# --- SEM010 -----------------------------------------------------------------------------
def check_set_null_on_not_null(schema: Schema) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            if fk.on_delete != "SET NULL":
                continue
            for name in fk.columns:
                col = table.column(name)
                if col is not None and not col.nullable:
                    out.append(
                        diag(
                            "warning",
                            SEM010,
                            f"ON DELETE SET NULL on NOT NULL column '{table.name}.{name}'; "
                            "the delete would fail",
                            fk.span,
                        )
                    )
    return out


SCHEMA_CHECKS: tuple[SchemaCheck, ...] = (
    check_fk_missing_table,
    check_fk_missing_columns,
    check_fk_column_count,
    check_fk_type_mismatch,
    check_fk_target_is_key,
    check_duplicate_columns,
    check_key_unknown_columns,
    check_no_primary_key,
    check_set_null_on_not_null,
)


# --- graph based: SEM009, SEM011 ---------------------------------------------------------
def find_cycles(sg: SchemaGraph) -> list[list[str]]:
    """Simple FK cycles of length >= 2, each rotated to start at its smallest table name."""
    simple: nx.DiGraph[str] = nx.DiGraph()
    simple.add_nodes_from(sg.graph.nodes)
    for u, v in sg.graph.edges():
        if u != v:
            simple.add_edge(u, v)
    cycles: list[list[str]] = []
    for cyc in nx.simple_cycles(simple):
        start = cyc.index(min(cyc))
        cycles.append(cyc[start:] + cyc[:start])
    return sorted(cycles)


def check_circular_fk(sg: SchemaGraph) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for cyc in find_cycles(sg):
        path = " -> ".join([*cyc, cyc[0]])
        table = sg.schema.tables[cyc[0]]
        out.append(diag("warning", SEM009, f"Circular foreign key chain: {path}", table.span))
    return out


def check_self_reference(sg: SchemaGraph) -> list[Diagnostic]:
    return [
        diag(
            "info",
            SEM011,
            f"Table '{r.child}' references itself via ({', '.join(r.fk.columns)}); "
            "it forms a hierarchy",
            r.fk.span,
        )
        for r in sg.self_references
    ]


# --- SEM012 -----------------------------------------------------------------------------
def _query_diag(q: Query, message: str) -> Diagnostic:
    return diag("error", SEM012, f"{q.id}: {message}", q.span)


def check_query(schema: Schema, q: Query) -> list[Diagnostic]:
    """Check one query's table and column references against the schema."""
    out: list[Diagnostic] = []
    scope: dict[str, Table] = {}  # alias or table name -> Table
    for tnode in q.ast.find_all(exp.Table):
        name = str(tnode.name)
        name = name if tnode.this is not None and tnode.this.args.get("quoted") else name.lower()
        table = schema.tables.get(name)
        if table is None:
            out.append(_query_diag(q, f"unknown table '{name}'"))
            continue
        scope[name] = table
        alias = tnode.alias
        if alias:
            scope[alias.lower()] = table
    select_aliases = {a.alias.lower() for a in q.ast.find_all(exp.Alias) if a.alias}
    # INSERT column list and UPDATE ... SET targets are not exp.Column in the same way.
    in_scope_tables = list({id(t): t for t in scope.values()}.values())
    for col in q.ast.find_all(exp.Column):
        cname = str(col.name)
        cname = cname if col.this is not None and col.this.args.get("quoted") else cname.lower()
        if isinstance(col.this, exp.Star):
            continue
        qualifier = col.table.lower() if col.table else ""
        if qualifier:
            owner = scope.get(qualifier)
            if owner is None:
                if schema.tables.get(qualifier) is None and qualifier not in scope:
                    out.append(_query_diag(q, f"unknown table or alias '{qualifier}'"))
                continue
            if owner.column(cname) is None:
                out.append(_query_diag(q, f"unknown column '{qualifier}.{cname}'"))
        else:
            owners = [t for t in in_scope_tables if t.column(cname) is not None]
            if not owners and cname not in select_aliases and in_scope_tables:
                out.append(_query_diag(q, f"unknown column '{cname}'"))
            elif len(owners) > 1:
                names = ", ".join(sorted(t.name for t in owners))
                out.append(_query_diag(q, f"ambiguous column '{cname}' (in {names})"))
    out.extend(_check_insert_columns(schema, q))
    return out


def _check_insert_columns(schema: Schema, q: Query) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    ast = q.ast
    if not isinstance(ast, exp.Insert) or not isinstance(ast.this, exp.Schema):
        return out
    table = schema.tables.get(str(ast.this.this.name).lower()) if ast.this.this else None
    if table is None:
        return out
    for ident in ast.this.expressions:
        if isinstance(ident, exp.Identifier):
            cname = str(ident.name) if ident.args.get("quoted") else str(ident.name).lower()
            if table.column(cname) is None:
                out.append(
                    _query_diag(q, f"unknown column '{cname}' in INSERT into '{table.name}'")
                )
    return out


def check_queries(schema: Schema, queries: Iterable[Query | TransactionBlock]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for item in queries:
        stmts = item.statements if isinstance(item, TransactionBlock) else (item,)
        for q in stmts:
            out.extend(check_query(schema, q))
    return out


def fk_ids(schema: Schema) -> list[str]:
    """Helper used by tests/IR: every FK identifier in the schema."""
    return [fk_id(t.name, fk) for t in schema.tables.values() for fk in t.foreign_keys]
