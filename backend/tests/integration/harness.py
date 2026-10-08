"""A compact SQL-vs-MongoDB comparison harness used by integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from bson import Binary, Decimal128

from schemashift.codegen.layout import build_layouts
from schemashift.codegen.migration import migration_plan
from schemashift.codegen.queries import QueryContext, TranslatedQuery, translate_all
from schemashift.codegen.runtime_migrate import migrate
from schemashift.codegen.values import materialize
from schemashift.models import PlacementPlan
from schemashift.pipeline import CompileOptions, CompileResult, compile_sql


def norm(value: Any) -> Any:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, Binary):
        return value.as_uuid() if value.subtype == 4 else bytes(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return Decimal(int(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    return value


def same(a: Any, b: Any) -> bool:
    a, b = norm(a), norm(b)
    if isinstance(a, (Decimal, float)) and isinstance(b, (Decimal, float)):
        x, y = float(a), float(b)
        return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))
    return bool(a == b)


def _row_key(row: tuple[Any, ...]) -> str:
    return repr(
        [
            round(float(norm(v)), 6) if isinstance(norm(v), (Decimal, float)) else norm(v)
            for v in row
        ]
    )


def rows_equal(left: list[tuple[Any, ...]], right: list[tuple[Any, ...]], ordered: bool) -> bool:
    if len(left) != len(right):
        return False
    if not ordered:
        key = _row_key
        left, right = sorted(left, key=key), sorted(right, key=key)
    return all(
        len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
        for a, b in zip(left, right, strict=True)
    )


@dataclass
class Outcome:
    query_id: str
    sql: str
    ok: bool
    pg: list[tuple[Any, ...]]
    mongo: list[tuple[Any, ...]]
    error: str | None = None


def run_select(pg_conn: Any, db: Any, tq: TranslatedQuery) -> Outcome:
    with pg_conn.cursor() as cur:
        cur.execute(tq.sql)
        pg_rows = [tuple(r) for r in cur.fetchall()]
    assert tq.pipeline is not None and tq.collection is not None
    docs = list(db[tq.collection].aggregate(materialize(tq.pipeline)))
    mongo_rows = [tuple(d.get(c) for c in tq.output_columns) for d in docs]
    return Outcome(
        tq.query_id, tq.sql, rows_equal(pg_rows, mongo_rows, tq.ordered), pg_rows, mongo_rows
    )


def setup_and_migrate(
    schema_sql: str,
    data_sql: str,
    queries_sql: str,
    plan: PlacementPlan | None,
    pg_conn: Any,
    schema_name: str,
    db: Any,
    options: CompileOptions | None = None,
) -> tuple[CompileResult, list[TranslatedQuery]]:
    """Create the SQL data, compile, create validators/indexes, migrate, translate queries."""
    from schemashift.codegen import generate_code

    options = options or CompileOptions()
    pg_conn.execute(schema_sql)  # trusted test fixture SQL
    if data_sql:
        pg_conn.execute(data_sql)
    result = compile_sql(schema_sql, queries_sql, "", options)
    chosen = plan if plan is not None else result.plan
    layouts = build_layouts(result.graph, chosen)
    generated = generate_code(result, options, run_id="it", timestamp="2026-01-01T00:00:00Z")
    ns: dict[str, Any] = {}
    # validators + indexes come from the generated code for the *optimizer's* plan, so rebuild for `chosen`
    from schemashift.codegen.collections import build_collections
    from schemashift.codegen.indexes import build_indexes

    for spec in build_collections(result.schema_, layouts, options.uuid_as).collections:
        db.create_collection(
            spec.name,
            validator=materialize(spec.validator),
            validationLevel=spec.validation_level,
            validationAction=spec.validation_action,
        )
    for ix in build_indexes(result.schema_, result.graph, layouts):
        kwargs: dict[str, Any] = {"name": ix.name}
        if ix.unique:
            kwargs["unique"] = True
        if ix.partial_filter:
            kwargs["partialFilterExpression"] = materialize(ix.partial_filter)
        db[ix.collection].create_index(list(ix.keys), **kwargs)
    mplan = migration_plan(result.schema_, layouts, options)
    migrate(pg_conn, db, mplan, schema=schema_name, log=lambda *_: None)
    ctx = QueryContext(
        result.schema_, result.graph, layouts, options.uuid_as, options.preserve_integer_ids
    )
    del generated, ns
    return result, translate_all(result.queries, ctx)
