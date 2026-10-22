"""Structured fuzz cases: schema, seed rows and SELECT queries, rendered to SQL text.

Everything is data (frozen models). The cases are rendered to SQL strings which then travel the
normal pipeline (parse -> compile -> codegen), so the fuzzer exercises exactly what a user would.
Rendered SQL is parsed and re-emitted from the AST before it runs on PostgreSQL.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from schemashift.models.base import FrozenModel

Kind = Literal[
    "integer", "bigint", "numeric", "text", "varchar", "boolean", "date", "timestamptz", "enum"
]
NUMERIC_KINDS: tuple[Kind, ...] = ("integer", "bigint", "numeric")
TEXT_KINDS: tuple[Kind, ...] = ("text", "varchar", "enum")


# ------------------------------------------------------------------------------ literals
def lit(value: Any) -> str:
    """SQL literal for a python value (None -> NULL)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dt.datetime):
        return "'" + value.isoformat() + "'"
    if isinstance(value, dt.date):
        return "'" + value.isoformat() + "'"
    return "'" + str(value).replace("'", "''") + "'"


# ------------------------------------------------------------------------------ predicates
class Pred(FrozenModel):
    """A boolean expression over qualified columns (``t.c``)."""

    op: Literal[
        "cmp", "in", "between", "null", "like", "bool", "not", "and", "or", "true", "unknown"
    ]
    column: str | None = None  # qualified: "t0.c1"
    rhs_column: str | None = None
    cmp: Literal["=", "<>", "<", "<=", ">", ">="] | None = None
    values: tuple[Any, ...] = ()  # literal(s); None means SQL NULL
    negated: bool = False  # IS NOT NULL / NOT IN / NOT BETWEEN / NOT LIKE
    ci: bool = False  # ILIKE
    items: tuple[Pred, ...] = ()

    def sql(self) -> str:
        o = self.op
        if o == "true":
            return "TRUE"
        if o == "cmp":
            right = self.rhs_column or lit(self.values[0])
            return f"{self.column} {self.cmp} {right}"
        if o == "in":
            body = ", ".join(lit(v) for v in self.values)
            return f"{self.column} {'NOT IN' if self.negated else 'IN'} ({body})"
        if o == "between":
            kw = "NOT BETWEEN" if self.negated else "BETWEEN"
            return f"{self.column} {kw} {lit(self.values[0])} AND {lit(self.values[1])}"
        if o == "null":
            return f"{self.column} IS {'NOT ' if self.negated else ''}NULL"
        if o == "like":
            word = "ILIKE" if self.ci else "LIKE"
            return f"{self.column} {'NOT ' if self.negated else ''}{word} {lit(self.values[0])}"
        if o == "bool":
            return f"{self.column}" if not self.negated else f"NOT {self.column}"
        if o == "not":
            return f"NOT ({self.items[0].sql()})"
        if o == "unknown":  # (p) IS NULL: the third truth value of SQL's three-valued logic
            return f"({self.items[0].sql()}) IS NULL"
        joiner = " AND " if o == "and" else " OR "
        return "(" + joiner.join(f"({i.sql()})" for i in self.items) + ")"


TRUE = Pred(op="true")


def p_not(p: Pred) -> Pred:
    return Pred(op="not", items=(p,))


def p_unknown(p: Pred) -> Pred:
    return Pred(op="unknown", items=(p,))


def p_and(*items: Pred) -> Pred:
    return Pred(op="and", items=tuple(items))


def p_or(*items: Pred) -> Pred:
    return Pred(op="or", items=tuple(items))


# ------------------------------------------------------------------------------ schema
class ColSpec(FrozenModel):
    name: str
    kind: Kind
    nullable: bool = True
    unique: bool = False
    precision: int = 10
    scale: int = 2
    length: int = 20
    enum_type: str | None = None
    check: str | None = None
    default: str | None = None

    def sql_type(self) -> str:
        k = self.kind
        if k == "numeric":
            return f"NUMERIC({self.precision},{self.scale})"
        if k == "varchar":
            return f"VARCHAR({self.length})"
        if k == "enum":
            return str(self.enum_type)
        return {"timestamptz": "TIMESTAMPTZ"}.get(k, k.upper())


class FkSpec(FrozenModel):
    column: str
    ref_table: str
    ref_column: str = "id"
    on_delete: Literal["NO ACTION", "CASCADE", "SET NULL", "RESTRICT"] = "NO ACTION"
    late: bool = False  # emitted as ALTER TABLE ... ADD CONSTRAINT after every CREATE TABLE


class TableSpec(FrozenModel):
    name: str
    pk: tuple[str, ...] = ("id",)
    serial_id: bool = True  # id SERIAL; False -> plain INTEGER keys
    columns: tuple[ColSpec, ...] = ()
    fks: tuple[FkSpec, ...] = ()

    def column(self, name: str) -> ColSpec | None:
        return next((c for c in self.columns if c.name == name), None)

    def kind_of(self, name: str) -> Kind:
        c = self.column(name)
        if c is not None:
            return c.kind
        return "integer"  # key columns


class SchemaSpec(FrozenModel):
    tables: tuple[TableSpec, ...]
    enums: dict[str, tuple[str, ...]] = {}

    def table(self, name: str) -> TableSpec:
        return next(t for t in self.tables if t.name == name)


def render_schema(schema: SchemaSpec) -> str:
    out: list[str] = []
    for name, values in schema.enums.items():
        out.append(f"CREATE TYPE {name} AS ENUM ({', '.join(lit(v) for v in values)});")
    late: list[str] = []
    for t in schema.tables:
        lines: list[str] = []
        key_cols = list(t.pk)
        for k in key_cols:
            if k == "id" and t.serial_id and len(key_cols) == 1:
                lines.append("id SERIAL")
            else:
                lines.append(f"{k} INTEGER NOT NULL")
        for c in t.columns:
            parts = [c.name, c.sql_type()]
            if not c.nullable:
                parts.append("NOT NULL")
            if c.unique:
                parts.append("UNIQUE")
            if c.default is not None:
                parts.append(f"DEFAULT {c.default}")
            if c.check:
                parts.append(f"CHECK ({c.check})")
            lines.append(" ".join(parts))
        lines.append(f"PRIMARY KEY ({', '.join(key_cols)})")
        for fk in t.fks:
            clause = f"FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})" + (
                f" ON DELETE {fk.on_delete}" if fk.on_delete != "NO ACTION" else ""
            )
            if fk.late:
                late.append(
                    f"ALTER TABLE {t.name} ADD CONSTRAINT fk_{t.name}_{fk.column} {clause};"
                )
            else:
                lines.append(clause)
        out.append(f"CREATE TABLE {t.name} (\n  " + ",\n  ".join(lines) + "\n);")
    out.extend(late)
    return "\n".join(out) + "\n"


Rows = dict[str, list[dict[str, Any]]]


def render_seed(schema: SchemaSpec, rows: Rows) -> str:
    stmts: list[str] = []
    for t in schema.tables:
        for row in rows.get(t.name, []):
            cols = list(row)
            stmts.append(
                f"INSERT INTO {t.name} ({', '.join(cols)}) "
                f"VALUES ({', '.join(lit(row[c]) for c in cols)});"
            )
    return "\n".join(stmts) + ("\n" if stmts else "")


# ------------------------------------------------------------------------------ queries
class JoinSpec(FrozenModel):
    kind: Literal["INNER", "LEFT"]
    table: str
    alias: str
    on_left: str  # qualified
    on_right: str  # qualified


class Agg(FrozenModel):
    fn: Literal["COUNT_STAR", "COUNT", "SUM", "AVG", "MIN", "MAX"]
    column: str | None = None  # qualified

    def sql(self) -> str:
        if self.fn == "COUNT_STAR":
            return "COUNT(*)"
        return f"{self.fn}({self.column})"


class SelectSpec(FrozenModel):
    table: str
    alias: str
    joins: tuple[JoinSpec, ...] = ()
    columns: tuple[str, ...] = ()  # qualified, plain projection
    expressions: tuple[str, ...] = ()  # computed projections, e.g. "a0.c0 / 2"
    aggs: tuple[Agg, ...] = ()
    group_by: tuple[str, ...] = ()
    where: Pred | None = None
    having: str | None = None  # rendered text over aggregates (rare)
    order_by: tuple[tuple[str, Literal["ASC", "DESC"]], ...] = ()  # qualified or alias
    limit: int | None = None
    offset: int | None = None
    distinct: bool = False

    def with_where(self, where: Pred | None) -> SelectSpec:
        return self.model_copy(update={"where": where})

    @property
    def is_aggregate(self) -> bool:
        return bool(self.aggs) or bool(self.group_by)


def render_select(s: SelectSpec) -> str:
    items: list[str] = []
    n = 0
    for c in s.columns:
        items.append(f"{c} AS c{n}")
        n += 1
    for e in s.expressions:
        items.append(f"{e} AS c{n}")
        n += 1
    for a in s.aggs:
        items.append(f"{a.sql()} AS c{n}")
        n += 1
    if not items:
        items.append("COUNT(*) AS c0")
    sql = f"SELECT {'DISTINCT ' if s.distinct else ''}{', '.join(items)} FROM {s.table} {s.alias}"
    for j in s.joins:
        sql += f" {j.kind} JOIN {j.table} {j.alias} ON {j.on_left} = {j.on_right}"
    if s.where is not None and s.where.op != "true":
        sql += f" WHERE {s.where.sql()}"
    if s.group_by:
        sql += f" GROUP BY {', '.join(s.group_by)}"
    if s.having:
        sql += f" HAVING {s.having}"
    if s.order_by:
        sql += " ORDER BY " + ", ".join(f"{c} {d}" for c, d in s.order_by)
    if s.limit is not None:
        sql += f" LIMIT {s.limit}"
    if s.offset is not None:
        sql += f" OFFSET {s.offset}"
    return sql + ";"


class FuzzCase(FrozenModel):
    schema_: SchemaSpec
    rows: Rows
    selects: tuple[SelectSpec, ...]
    seed: int = 0

    def schema_sql(self) -> str:
        return render_schema(self.schema_)

    def seed_sql(self) -> str:
        return render_seed(self.schema_, self.rows)

    def queries_sql(self) -> str:
        return "\n".join(render_select(s) for s in self.selects)
