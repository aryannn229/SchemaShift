"""Hypothesis strategies for schemas, seed data and SELECT queries.

Generated schemas stay inside the supported DDL subset and are valid by construction
(unique values really are unique, foreign keys point at existing rows, CHECKs are satisfied),
so a failure is a SchemaShift finding rather than a malformed input. Seed data is deliberately
nasty: NULLs, empty strings, duplicates, boundary numbers and empty tables.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from hypothesis import strategies as st

from schemashift.fuzz.spec import (
    NUMERIC_KINDS,
    TEXT_KINDS,
    Agg,
    ColSpec,
    FkSpec,
    FuzzCase,
    JoinSpec,
    Kind,
    Pred,
    Rows,
    SchemaSpec,
    SelectSpec,
    TableSpec,
    p_and,
    p_not,
    p_or,
)

ENUM_VALUES = ("draft", "open", "closed")
# Letters and digits only: PostgreSQL's libc collation ignores punctuation and spaces at the first
# comparison level while ICU does not, so strings differing only in punctuation can sort differently
# (documented limitation, see docs/CODEGEN.md "String collation").
TEXT_POOL = ("", "a", "A", "b", "ab", "Ab", "abc", "10", "9", "z", "m", "B")
INT_POOL = (0, 1, -1, 2, 3, 5, 7, 10, 100, -100, 2147483647, -2147483648)
BIG_POOL = (0, 1, -1, 42, 1000000007, 2**40, -(2**40), 2**53, 2**53 + 1, 2**62)
NUM_POOL = (
    Decimal("0"),
    Decimal("0.00"),
    Decimal("1.50"),
    Decimal("-1.50"),
    Decimal("2.25"),
    Decimal("99999999.99"),
    Decimal("-99999999.99"),
    Decimal("0.10"),
    Decimal("0.01"),
    Decimal("3.33"),
)
DATE_POOL = (dt.date(2020, 1, 1), dt.date(2021, 6, 15), dt.date(1999, 12, 31), dt.date(2024, 2, 29))
TS_POOL = (
    dt.datetime(2020, 1, 1, 0, 0, 0, tzinfo=dt.UTC),
    dt.datetime(2021, 6, 15, 12, 30, 45, tzinfo=dt.UTC),
    dt.datetime(2023, 3, 1, 23, 59, 59, 999000, tzinfo=dt.UTC),
    dt.datetime(2022, 11, 5, 8, 0, 0, tzinfo=dt.UTC),
)


def pool_for(c: ColSpec) -> tuple[Any, ...]:
    k = c.kind
    if k == "integer":
        return INT_POOL
    if k == "bigint":
        return BIG_POOL
    if k == "numeric":
        scale = Decimal(1).scaleb(-c.scale)
        return tuple(
            v.quantize(scale) for v in NUM_POOL if abs(v) < Decimal(10) ** (c.precision - c.scale)
        )
    if k in ("text", "varchar"):
        return tuple(v for v in TEXT_POOL if len(v) <= c.length)
    if k == "enum":
        return ENUM_VALUES
    if k == "boolean":
        return (True, False)
    if k == "date":
        return DATE_POOL
    return TS_POOL


# ------------------------------------------------------------------------------ schema
@st.composite
def column_specs(draw: st.DrawFn, idx: int, enum_name: str) -> ColSpec:
    kind: Kind = draw(
        st.sampled_from(
            [
                "integer",
                "bigint",
                "numeric",
                "text",
                "varchar",
                "boolean",
                "date",
                "timestamptz",
                "enum",
            ]
        )
    )
    nullable = draw(st.booleans())
    kw: dict[str, Any] = {"name": f"c{idx}", "kind": kind, "nullable": nullable}
    if kind == "numeric":
        kw["precision"] = draw(st.sampled_from([10, 12]))
        kw["scale"] = draw(st.sampled_from([0, 2]))
    if kind == "varchar":
        kw["length"] = draw(st.sampled_from([10, 20]))
    if kind == "enum":
        kw["enum_type"] = enum_name
    if kind in ("integer", "bigint") and draw(st.integers(0, 5)) == 0:
        kw["check"] = f"c{idx} >= -2147483648"  # always satisfied; exercises CHECK translation
    if draw(st.integers(0, 6)) == 0 and kind in ("integer", "text", "varchar", "numeric"):
        kw["default"] = {"integer": "0", "numeric": "0", "text": "'d'", "varchar": "'d'"}[kind]
    return ColSpec(**kw)


@st.composite
def schemas(draw: st.DrawFn, max_tables: int = 3) -> SchemaSpec:
    n = draw(st.integers(1, max_tables))
    tables: list[TableSpec] = []
    names = [f"t{i}" for i in range(n)]
    composite_last = n > 1 and draw(st.integers(0, 5)) == 0  # last table has a composite key
    for i, name in enumerate(names):
        ncols = draw(st.integers(1, 4))
        cols = tuple(draw(column_specs(j, "mood")) for j in range(ncols))
        # make about a third of the unique-capable columns UNIQUE
        cols = tuple(
            c.model_copy(update={"unique": True})
            if draw(st.integers(0, 4)) == 0 and c.kind in ("integer", "bigint", "text", "varchar")
            else c
            for c in cols
        )
        fks: list[FkSpec] = []
        extra_cols: list[ColSpec] = []
        # foreign keys to earlier tables, or to itself (hierarchy), or late to a later table (cycle)
        is_composite = composite_last and i == n - 1
        parents = names[: n - 1] if composite_last else names
        candidates = [(names[j], False) for j in range(i) if names[j] in parents]
        if not is_composite:
            candidates.append((name, False))
        if i + 1 < n and names[i + 1] in parents:
            candidates.append((names[i + 1], True))
        if is_composite:
            candidates = []
        for ref, late in candidates:
            if draw(st.integers(0, 2)) != 0:
                continue
            col = f"f{len(fks)}"
            nullable = True if (ref == name or late) else draw(st.booleans())
            extra_cols.append(ColSpec(name=col, kind="integer", nullable=nullable))
            on_delete = draw(st.sampled_from(["NO ACTION", "CASCADE", "SET NULL", "RESTRICT"]))
            if on_delete == "SET NULL" and not nullable:
                on_delete = "NO ACTION"
            fks.append(FkSpec(column=col, ref_table=ref, on_delete=on_delete, late=late))
        pk: tuple[str, ...] = ("id", "k2") if is_composite else ("id",)
        tables.append(
            TableSpec(
                name=name,
                pk=pk,
                serial_id=len(pk) == 1,
                columns=cols + tuple(extra_cols),
                fks=tuple(fks),
            )
        )
    return SchemaSpec(tables=tuple(tables), enums={"mood": ENUM_VALUES})


# ------------------------------------------------------------------------------ seed data
@st.composite
def seed_rows(draw: st.DrawFn, schema: SchemaSpec) -> Rows:
    rows: Rows = {}
    ids: dict[str, list[int]] = {}
    for t in schema.tables:
        count = draw(st.sampled_from([0, 1, 2, 3, 5, 8]))
        used_unique: dict[str, set[Any]] = {c.name: set() for c in t.columns if c.unique}
        table_rows: list[dict[str, Any]] = []
        for r in range(count):
            row: dict[str, Any] = {"id": r + 1}
            if "k2" in t.pk:
                row["k2"] = draw(st.integers(0, 2))
                if any(x["id"] == row["id"] and x["k2"] == row["k2"] for x in table_rows):
                    continue
            for c in t.columns:
                fk = next((f for f in t.fks if f.column == c.name), None)
                if fk is not None:
                    pool = ids.get(fk.ref_table, [])
                    if fk.ref_table == t.name:
                        pool = [x["id"] for x in table_rows]  # earlier rows of this table
                    if fk.late:
                        pool = []  # cyclic FK stays NULL in the seed
                    options: list[Any] = list(pool)
                    if c.nullable or not options:
                        options.append(None)
                    value = draw(st.sampled_from(options))
                    if value is None and not c.nullable:
                        value = None  # parent table empty: row invalid, filtered below
                    row[c.name] = value
                    continue
                pool_v: tuple[Any, ...] = pool_for(c)
                choices: list[Any] = list(pool_v)
                if c.nullable:
                    choices = choices + [None] * 2  # NULLs are common
                value = draw(st.sampled_from(choices))
                if c.unique and value is not None:
                    if value in used_unique[c.name]:
                        value = None if c.nullable else None
                    else:
                        used_unique[c.name].add(value)
                row[c.name] = value
            if any(row[c.name] is None for c in t.columns if not c.nullable):
                continue  # cannot satisfy NOT NULL here; drop the row
            table_rows.append(row)
        rows[t.name] = table_rows
        ids[t.name] = [x["id"] for x in table_rows]
    return rows


# ------------------------------------------------------------------------------ queries
def _qualified(alias: str, c: str) -> str:
    return f"{alias}.{c}"


def literal_for(kind: Kind, c: ColSpec | None) -> st.SearchStrategy[Any]:
    spec = c or ColSpec(name="x", kind=kind)
    pool = pool_for(spec)
    return st.sampled_from(pool) if pool else st.just(0)


@st.composite
def predicates(
    draw: st.DrawFn,
    columns: list[tuple[str, ColSpec]],
    depth: int = 2,
) -> Pred:
    """A random boolean predicate over ``(qualified_name, spec)`` columns."""
    if not columns:
        return Pred(op="true")
    if depth > 0 and draw(st.integers(0, 3)) == 0:
        kind = draw(st.sampled_from(["and", "or", "not"]))
        if kind == "not":
            return p_not(draw(predicates(columns, depth - 1)))
        a = draw(predicates(columns, depth - 1))
        b = draw(predicates(columns, depth - 1))
        return p_and(a, b) if kind == "and" else p_or(a, b)
    qname, col = draw(st.sampled_from(columns))
    k = col.kind
    ops = ["null", "null_not"]
    if k == "boolean":
        return draw(
            st.one_of(
                st.just(Pred(op="bool", column=qname)),
                st.just(Pred(op="bool", column=qname, negated=True)),
                st.just(Pred(op="null", column=qname)),
                st.just(Pred(op="null", column=qname, negated=True)),
            )
        )
    ops += ["cmp", "cmp", "in", "not_in"]
    if k in NUMERIC_KINDS or k in ("date", "timestamptz", "text", "varchar", "enum"):
        ops += ["between", "not_between"]
    if k in ("text", "varchar"):
        ops += ["like", "not_like"]
    choice = draw(st.sampled_from(ops))
    if choice == "null":
        return Pred(op="null", column=qname)
    if choice == "null_not":
        return Pred(op="null", column=qname, negated=True)
    if choice == "cmp":
        cmp = draw(st.sampled_from(["=", "<>", "<", "<=", ">", ">="]))
        value = draw(literal_for(k, col))
        return Pred(op="cmp", column=qname, cmp=cmp, values=(value,))
    if choice in ("in", "not_in"):
        vals = draw(st.lists(literal_for(k, col), min_size=1, max_size=3))
        if draw(st.integers(0, 3)) == 0:
            vals.append(None)  # NULL inside the list: the classic NOT IN trap
        return Pred(op="in", column=qname, values=tuple(vals), negated=choice == "not_in")
    if choice in ("between", "not_between"):
        a = draw(literal_for(k, col))
        b = draw(literal_for(k, col))
        lo, hi = sorted([a, b])
        return Pred(op="between", column=qname, values=(lo, hi), negated=choice == "not_between")
    pattern = draw(st.sampled_from(["a%", "%b", "%a%", "_", "ab", "%", "", "A%", "%B"]))
    return Pred(
        op="like",
        column=qname,
        values=(pattern,),
        negated=choice == "not_like",
        ci=draw(st.booleans()),
    )


def _alias(i: int) -> str:
    return f"a{i}"


@st.composite
def selects(draw: st.DrawFn, schema: SchemaSpec, allow_aggregates: bool = True) -> SelectSpec:
    base = draw(st.sampled_from(schema.tables))
    aliases = {base.name: _alias(0)}
    joins: list[JoinSpec] = []
    tables_in = [base]
    # up to two joins along FK edges (either direction)
    for _ in range(draw(st.integers(0, 2))):
        edges: list[tuple[TableSpec, str, str, TableSpec]] = []
        for t in tables_in:
            for fk in t.fks:
                edges.append((t, fk.column, fk.ref_column, schema.table(fk.ref_table)))
            for other in schema.tables:
                for fk in other.fks:
                    if fk.ref_table == t.name:
                        edges.append((t, fk.ref_column, fk.column, other))
        if not edges:
            break
        t_from, c_from, c_to, t_to = draw(st.sampled_from(edges))
        if t_to.name in aliases:
            break  # keep aliasing simple: no repeated tables / self-joins
        alias = _alias(len(aliases))
        aliases[t_to.name] = alias
        joins.append(
            JoinSpec(
                kind=draw(st.sampled_from(["INNER", "LEFT"])),
                table=t_to.name,
                alias=alias,
                on_left=_qualified(aliases[t_from.name], c_from),
                on_right=_qualified(alias, c_to),
            )
        )
        tables_in.append(t_to)

    def cols_of(t: TableSpec) -> list[tuple[str, ColSpec]]:
        out = [
            (_qualified(aliases[t.name], "id"), ColSpec(name="id", kind="integer", nullable=False))
        ]
        out += [(_qualified(aliases[t.name], c.name), c) for c in t.columns]
        return out

    all_cols = [qc for t in tables_in for qc in cols_of(t)]
    base_cols = cols_of(base)
    where = draw(st.one_of(st.none(), predicates(all_cols)))
    numeric = [qc for qc in all_cols if qc[1].kind in NUMERIC_KINDS]
    mode = draw(
        st.sampled_from(
            ["rows", "rows", "distinct", "group", "global"] if allow_aggregates else ["rows"]
        )
    )
    if mode == "rows":
        picked = draw(
            st.lists(st.sampled_from(all_cols), min_size=1, max_size=3, unique_by=lambda x: x[0])
        )
        cols = tuple(q for q, _ in picked)
        # always project the base key and tiebreak on every table's key for a total order
        key_cols = tuple(_qualified(aliases[t.name], k) for t in tables_in for k in t.pk)
        proj = tuple(dict.fromkeys((*cols, *key_cols)))
        expressions: tuple[str, ...] = ()
        if numeric and draw(st.integers(0, 2)) == 0:
            qn, _ = draw(st.sampled_from(numeric))
            op = draw(st.sampled_from(["+", "-", "*", "/"]))
            operand = draw(st.sampled_from([1, 2, 3, -2, 7]))
            expressions = (f"{qn} {op} {operand}",)
        order: list[tuple[str, Any]] = []
        if draw(st.booleans()):
            oc = draw(st.sampled_from(all_cols))[0]
            order.append((oc, draw(st.sampled_from(["ASC", "DESC"]))))
        order.extend((k, "ASC") for k in key_cols)
        limit = draw(st.one_of(st.none(), st.integers(0, 5)))
        offset = draw(st.one_of(st.none(), st.integers(0, 3))) if limit is not None else None
        return SelectSpec(
            table=base.name,
            alias=aliases[base.name],
            joins=tuple(joins),
            columns=proj,
            expressions=expressions,
            where=where,
            order_by=tuple(order),
            limit=limit,
            offset=offset,
        )
    if mode == "distinct":
        pool = [qc for qc in all_cols if qc[1].kind != "boolean"] or all_cols
        picked_d = draw(
            st.lists(st.sampled_from(pool), min_size=1, max_size=2, unique_by=lambda x: x[0])
        )
        dcols = tuple(q for q, _ in picked_d)
        return SelectSpec(
            table=base.name,
            alias=aliases[base.name],
            joins=tuple(joins),
            columns=dcols,
            where=where,
            distinct=True,
            order_by=tuple((c, "ASC") for c in dcols),
        )
    orderable = [qc for qc in all_cols if qc[1].kind in ("integer", "bigint", "numeric", "date")]
    aggs: list[Agg] = [Agg(fn="COUNT_STAR")]
    for _ in range(draw(st.integers(0, 2))):
        fn = draw(st.sampled_from(["COUNT", "SUM", "AVG", "MIN", "MAX"]))
        pool = numeric if fn in ("SUM", "AVG") else orderable
        if pool:
            aggs.append(Agg(fn=fn, column=draw(st.sampled_from(pool))[0]))
    if mode == "global":
        return SelectSpec(
            table=base.name,
            alias=aliases[base.name],
            joins=tuple(joins),
            aggs=tuple(aggs),
            where=where,
        )
    group_pool = [
        qc for qc in all_cols if qc[1].kind in ("integer", "text", "varchar", "boolean", "enum")
    ]
    if not group_pool:
        group_pool = base_cols[:1]
    group = tuple(
        q
        for q, _ in draw(
            st.lists(st.sampled_from(group_pool), min_size=1, max_size=2, unique_by=lambda x: x[0])
        )
    )
    having = None
    if draw(st.integers(0, 3)) == 0:
        having = f"COUNT(*) > {draw(st.integers(0, 2))}"
    return SelectSpec(
        table=base.name,
        alias=aliases[base.name],
        joins=tuple(joins),
        columns=group,
        aggs=tuple(aggs),
        group_by=group,
        where=where,
        having=having,
        order_by=tuple((g, "ASC") for g in group),
    )


@st.composite
def cases(draw: st.DrawFn, max_tables: int = 3, max_queries: int = 4) -> FuzzCase:
    schema = draw(schemas(max_tables))
    rows = draw(seed_rows(schema))
    queries = draw(st.lists(selects(schema), min_size=1, max_size=max_queries))
    return FuzzCase(schema_=schema, rows=rows, selects=tuple(queries))


__all__ = [
    "TEXT_KINDS",
    "cases",
    "column_specs",
    "predicates",
    "schemas",
    "seed_rows",
    "selects",
]
