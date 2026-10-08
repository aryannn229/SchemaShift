"""Run the original SQL on PostgreSQL and the generated MongoDB code on identical seed data."""

from __future__ import annotations

import time
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb
from pymongo.errors import PyMongoError

from schemashift.codegen import CodegenResult, generate_code
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.codegen.queries import QueryContext, TranslatedQuery, WriteOp, translate_query
from schemashift.codegen.values import materialize
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query
from schemashift.optimizer.workload import flatten
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions, CompileResult
from schemashift.verification.ddl import UnsafeSqlError, assert_safe_expression, ddl_statements, q
from schemashift.verification.diff import ResultDiff, RowDiff, compare
from schemashift.verification.sandbox import Sandbox, SandboxConfig
from schemashift.verification.seed import (
    DEFAULT_ROWS_PER_TABLE,
    MAX_ROWS_PER_TABLE,
    SeedData,
    SeedError,
    generate_seed,
    seed_from_inserts,
)

MAX_QUERIES = 30
MAX_TABLES = 50
MAX_TIME_MS = 5000

Status = Literal["MATCH", "MISMATCH", "ERROR", "NOT_TRANSLATED", "SKIPPED"]


class VerificationError(Exception):
    """The run cannot start (invalid input, limits exceeded)."""


class QueryOutcome(FrozenModel):
    query_id: str
    sql: str
    kind: str
    status: Status
    pg_rows: int = 0
    mongo_rows: int = 0
    differing: list[RowDiff] = []
    hypothesis: str | None = None
    explained_by: list[str] = []
    error: str | None = None
    pg_error: str | None = None


class ProbeOutcome(FrozenModel):
    verdict_id: str
    rule_id: str
    status: str  # the verdict status this probe backs up
    description: str
    postgres: str
    mongo: str
    demonstrates: bool
    tables_differing: list[str] = []
    examples: list[RowDiff] = []


class VerificationReport(FrozenModel):
    run_id: str
    seed: int
    rows: dict[str, int]
    migrated: dict[str, int]
    queries: list[QueryOutcome]
    probes: list[ProbeOutcome] = []
    warnings: list[str] = []
    duration_ms: int = 0

    @property
    def verified(self) -> list[QueryOutcome]:
        return [o for o in self.queries if o.status in ("MATCH", "MISMATCH")]

    @property
    def correctness(self) -> float:
        done = self.verified
        return sum(o.status == "MATCH" for o in done) / len(done) if done else 1.0

    @property
    def unexplained(self) -> list[QueryOutcome]:
        return [o for o in self.queries if o.status == "MISMATCH" and not o.explained_by]


# ------------------------------------------------------------------------- helpers
def run_python_source(source: str, entry: str, *args: Any) -> Any:
    """Execute generated (our own) Python and call ``entry``."""
    ns: dict[str, Any] = {"__name__": "generated"}
    exec(compile(source, "<generated>", "exec"), ns)  # noqa: S102
    return ns[entry](*args) if args else ns


def insert_seed(pg: Any, schema: Any, seed: SeedData) -> dict[str, int]:
    """Parameterized INSERTs built from the schema (identifiers quoted, values bound)."""
    counts: dict[str, int] = {}
    for name in schema.tables:
        rows = seed.rows.get(name, [])
        omitted = seed.omitted.get(name, [])
        types = {c.name: c.sql_type for c in schema.tables[name].columns}
        for i, row in enumerate(rows):
            skip = set(omitted[i]) if i < len(omitted) else set()
            cols = [c for c in row if c not in skip]
            values = [
                Jsonb(row[c])
                if types[c].base in ("JSON", "JSONB") and row[c] is not None
                else row[c]
                for c in cols
            ]
            stmt = (
                f"INSERT INTO {q(name)} ({', '.join(q(c) for c in cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})"
            )
            pg.execute(stmt, values)
        counts[name] = len(rows)
        for col in schema.tables[name].columns:
            if col.is_identity and rows:
                top = max(
                    (r[col.name] for r in rows if isinstance(r.get(col.name), int)), default=0
                )
                # keep the identity sequence ahead of explicitly seeded ids
                pg.execute(
                    f"ALTER TABLE {q(name)} ALTER COLUMN {q(col.name)} RESTART WITH {int(top) + 1}"
                )
    return counts


def apply_ops(db: Any, ops: list[WriteOp]) -> None:
    for op in ops:
        coll = db[op.collection]
        if op.op == "insertOne":
            coll.insert_one(materialize((op.documents or [{}])[0]))
        elif op.op == "insertMany":
            coll.insert_many(materialize(op.documents or []))
        elif op.op == "deleteMany":
            coll.delete_many(materialize(op.filter or {}))
        else:
            kwargs = {"array_filters": materialize(op.array_filters)} if op.array_filters else {}
            method = coll.update_one if op.op == "updateOne" else coll.update_many
            res = method(materialize(op.filter or {}), materialize(op.update), **kwargs)
            if op.op == "updateOne" and res.matched_count == 0:
                raise PyMongoError("no parent document matched the filter")


def pg_rows(pg: Any, sql: str) -> list[tuple[Any, ...]]:
    with pg.cursor() as cur:
        cur.execute(sql)
        return [tuple(r) for r in cur.fetchall()]


def mongo_rows(db: Any, tq: TranslatedQuery) -> list[tuple[Any, ...]]:
    assert tq.pipeline is not None and tq.collection is not None
    docs = db[tq.collection].aggregate(materialize(tq.pipeline), maxTimeMS=MAX_TIME_MS)
    return [tuple(d.get(c) for c in tq.output_columns) for d in docs]


def safe_sql(query: Query, schema: Any) -> str:
    """SQL re-emitted from the validated AST (never the raw user string)."""
    assert_safe_expression(query.ast, schema)
    return str(query.ast.sql(dialect="postgres"))


class Env:
    """Everything needed to run statements on both sides of one sandbox."""

    def __init__(
        self,
        result: CompileResult,
        codegen: CodegenResult,
        layouts: dict[str, TableLayout],
        sb: Sandbox,
        options: CompileOptions,
    ) -> None:
        self.result, self.codegen, self.layouts, self.sb, self.options = (
            result,
            codegen,
            layouts,
            sb,
            options,
        )
        self.schema = result.schema_
        self.ctx = QueryContext(
            result.schema_, result.graph, layouts, options.uuid_as, options.preserve_integer_ids
        )
        self.migrate_ns: dict[str, Any] | None = None

    def table_select(self, table: str) -> TranslatedQuery:
        t = self.schema.tables[table]
        order = ", ".join(
            q(c) for c in (t.primary_key.columns if t.primary_key else [c.name for c in t.columns])
        )
        parsed = parse(f"SELECT * FROM {q(table)} ORDER BY {order};", "queries").all_queries()[0]
        tq = translate_query(parsed, self.ctx)
        if tq.error:
            raise VerificationError(f"cannot snapshot {table}: {tq.error}")
        return tq

    def snapshot_tables(self) -> dict[str, ResultDiff]:
        """Compare every table between PostgreSQL and MongoDB."""
        out: dict[str, ResultDiff] = {}
        for table in self.schema.tables:
            tq = self.table_select(table)
            left = pg_rows(self.sb.pg, f"SELECT * FROM {q(table)}")
            out[table] = compare(left, mongo_rows(self.sb.mongo_db, tq), ordered=False)
        return out

    def migrate(self) -> dict[str, int]:
        if self.migrate_ns is None:
            self.migrate_ns = run_python_source(self.codegen.file("python/migrate.py").content, "")
        ns = self.migrate_ns
        assert ns is not None
        return dict(
            ns["migrate"](
                self.sb.pg, self.sb.mongo_db, ns["PLAN"], schema=self.sb.name, log=lambda *_: None
            )
        )

    def resync(self) -> None:
        """Re-create the MongoDB data from the current PostgreSQL state."""
        for coll in self.sb.mongo_db.list_collection_names():
            if not coll.startswith("_"):
                self.sb.mongo_db[coll].delete_many({})
        self.migrate()


def verify(
    result: CompileResult,
    options: CompileOptions | None = None,
    config: SandboxConfig | None = None,
    *,
    seed_sql: str = "",
    seed: int = 0,
    run_probes: bool = True,
) -> VerificationReport:
    """Verify a compile result against real PostgreSQL and MongoDB sandboxes."""
    if config is None:
        raise VerificationError("a SandboxConfig is required")
    options = options or CompileOptions()
    if result.has_errors:
        raise VerificationError("fix the schema errors before verifying")
    if len(result.schema_.tables) > MAX_TABLES:
        raise VerificationError(f"at most {MAX_TABLES} tables can be verified")
    flat = flatten(result.queries)
    if len(flat) > MAX_QUERIES:
        raise VerificationError(f"at most {MAX_QUERIES} queries can be verified")
    rows_per_table = max(1, min(options.rows_per_table, MAX_ROWS_PER_TABLE))
    started = time.monotonic()
    warnings: list[str] = []
    codegen = generate_code(result, options, run_id="verify")
    layouts = build_layouts(result.graph, result.plan)
    statements, ddl_warnings = ddl_statements(result.schema_)
    warnings.extend(ddl_warnings)
    try:
        seed_data = _seed(result, seed_sql, seed, rows_per_table)
    except SeedError as exc:
        raise VerificationError(str(exc)) from exc
    warnings.extend(seed_data.warnings)

    with Sandbox(config) as sb:
        for stmt in statements:
            sb.pg.execute(stmt)
        rows = insert_seed(sb.pg, result.schema_, seed_data)
        run_python_source(
            codegen.file("python/schema.py").content, "create_collections", sb.mongo_db
        )
        run_python_source(codegen.file("python/indexes.py").content, "create_indexes", sb.mongo_db)
        env = Env(result, codegen, layouts, sb, options)
        migrated = env.migrate()
        outcomes: list[QueryOutcome] = []
        translated = {t.query_id: t for t in codegen.queries}
        for query in flat:
            tq = translated[query.id]
            if query.kind == "SELECT":
                outcomes.append(_run_select(env, query, tq))
        for query in flat:
            if query.kind != "SELECT":
                outcomes.append(_run_dml(env, query, translated[query.id]))
        probes: list[ProbeOutcome] = []
        if run_probes:
            from schemashift.verification.probes import run_probes as _probes

            probes = _probes(env)
    return VerificationReport(
        run_id=sb.run_id,
        seed=seed_data.seed,
        rows=rows,
        migrated=migrated,
        queries=sorted(outcomes, key=lambda o: _order_key(o.query_id)),
        probes=probes,
        warnings=warnings,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _order_key(query_id: str) -> int:
    digits = "".join(ch for ch in query_id if ch.isdigit())
    return int(digits) if digits else 0


def _seed(result: CompileResult, seed_sql: str, seed: int, rows: int) -> SeedData:
    if seed_sql.strip():
        user = seed_from_inserts(result.schema_, result.seed_queries, seed)
        if user.rows:
            return user
    return generate_seed(result.schema_, seed, rows or DEFAULT_ROWS_PER_TABLE)


def explaining_verdicts(env: Env, query_id: str) -> list[str]:
    """CHANGED/BROKEN verdicts of the query's own IR nodes (joins, aggregates)."""
    out: list[str] = []
    for v in env.result.equivalence.final.verdicts:
        if v.status == "SAFE":
            continue
        node = env.result.ir.by_id(v.node_id)
        if getattr(node, "query_id", None) == query_id:
            out.append(v.node_id)
    return out


def _run_select(env: Env, query: Query, tq: TranslatedQuery) -> QueryOutcome:
    base = {"query_id": query.id, "sql": " ".join(query.raw_sql.split()), "kind": "SELECT"}
    if tq.error:
        return QueryOutcome(**base, status="NOT_TRANSLATED", error=tq.error)
    try:
        sql = safe_sql(query, env.schema)
    except UnsafeSqlError as exc:
        return QueryOutcome(**base, status="SKIPPED", error=str(exc))
    try:
        left = pg_rows(env.sb.pg, sql)
    except psycopg.Error as exc:
        return QueryOutcome(**base, status="ERROR", pg_error=str(exc).splitlines()[0])
    try:
        right = mongo_rows(env.sb.mongo_db, tq)
    except PyMongoError as exc:
        return QueryOutcome(**base, status="ERROR", error=str(exc))
    diff = compare(left, right, tq.ordered, tq.order_positions, tq.global_aggregate)
    explained = explaining_verdicts(env, query.id) if diff.status == "MISMATCH" else []
    return QueryOutcome(
        **base,
        status=diff.status,
        pg_rows=diff.pg_rows,
        mongo_rows=diff.mongo_rows,
        differing=diff.differing,
        hypothesis=diff.hypothesis,
        explained_by=explained,
    )


def snapshot_mongo(db: Any) -> dict[str, list[dict[str, Any]]]:
    return {c: list(db[c].find()) for c in db.list_collection_names() if not c.startswith("_")}


def restore_mongo(db: Any, snap: dict[str, list[dict[str, Any]]]) -> None:
    for coll, docs in snap.items():
        db[coll].delete_many({})
        if docs:
            db[coll].insert_many(docs)


def _run_dml(env: Env, query: Query, tq: TranslatedQuery) -> QueryOutcome:
    base = {"query_id": query.id, "sql": " ".join(query.raw_sql.split()), "kind": query.kind}
    if tq.error:
        return QueryOutcome(**base, status="NOT_TRANSLATED", error=tq.error)
    try:
        sql = safe_sql(query, env.schema)
    except UnsafeSqlError as exc:
        return QueryOutcome(**base, status="SKIPPED", error=str(exc))
    pg, db = env.sb.pg, env.sb.mongo_db
    generated = _generated_columns(env, query)
    snap = snapshot_mongo(db)
    try:
        if generated:
            cols = ", ".join(q(c) for c in generated)
            returned = [tuple(r) for r in pg.execute(f"{sql} RETURNING {cols}").fetchall()]
            _inject_generated(env, query, tq, generated, returned)
        else:
            pg.execute(sql)
    except psycopg.errors.IntegrityError as exc:
        message = str(exc).splitlines()[0]
        try:
            apply_ops(db, tq.operations)
            mongo_accepted = True
        except PyMongoError:
            mongo_accepted = False
        restore_mongo(db, snap)
        if not mongo_accepted:
            return QueryOutcome(
                **base,
                status="MATCH",
                pg_error=message,
                hypothesis="both databases rejected the statement (enforced on both sides)",
            )
        return QueryOutcome(
            **base,
            status="MISMATCH",
            pg_error=message,
            hypothesis=(
                "PostgreSQL rejected the statement but MongoDB accepted its translation "
                "(no native enforcement): use the generated helper function"
            ),
            explained_by=_dml_verdicts(env, query),
        )
    except psycopg.Error as exc:
        return QueryOutcome(**base, status="ERROR", pg_error=str(exc).splitlines()[0])
    try:
        apply_ops(db, tq.operations)
    except PyMongoError as exc:
        restore_mongo(db, snap)
        return QueryOutcome(
            **base,
            status="MISMATCH",
            error=str(exc).splitlines()[0],
            hypothesis="PostgreSQL accepted the statement but MongoDB rejected its translation",
            explained_by=_dml_verdicts(env, query),
        )
    diffs = env.snapshot_tables()
    bad = {t: d for t, d in diffs.items() if d.status == "MISMATCH"}
    if not bad:
        return QueryOutcome(**base, status="MATCH")
    env.resync()  # keep later statements independent of this divergence
    first = next(iter(bad.values()))
    return QueryOutcome(
        **base,
        status="MISMATCH",
        pg_rows=first.pg_rows,
        mongo_rows=first.mongo_rows,
        differing=first.differing,
        hypothesis=(
            "tables differ after the statement: "
            + ", ".join(sorted(bad))
            + " (PostgreSQL applied a cascade / SET NULL / key propagation that MongoDB does not)"
        ),
        explained_by=_dml_verdicts(env, query),
    )


def _generated_columns(env: Env, query: Query) -> list[str]:
    """Columns an INSERT leaves to the database whose value PostgreSQL computes (identity ids,
    now() / expression defaults): their value must be mirrored into the MongoDB document."""
    from sqlglot import exp

    from schemashift.optimizer.workload import write_target

    if query.kind != "INSERT":
        return []
    table = env.schema.tables.get(write_target(query) or "")
    if table is None:
        return []
    target = query.ast.this
    given = (
        {str(i.name).lower() for i in target.expressions if isinstance(i, exp.Identifier)}
        if isinstance(target, exp.Schema) and target.expressions
        else {c.name for c in table.columns}
    )
    return [
        c.name
        for c in table.columns
        if c.name not in given
        and (c.is_identity or (c.default is not None and c.default.kind in ("now", "expression")))
    ]


def _inject_generated(
    env: Env, query: Query, tq: TranslatedQuery, columns: list[str], returned: list[tuple[Any, ...]]
) -> None:
    """Copy PostgreSQL's generated values into the MongoDB documents (the id map)."""
    from schemashift.codegen.runtime_migrate import convert_value
    from schemashift.optimizer.workload import write_target

    table = write_target(query) or ""
    layout = env.layouts[table]
    plan = env.migrate_ns["PLAN"] if env.migrate_ns else {}
    docs: list[dict[str, Any]] = []
    for op in tq.operations:
        if op.documents:
            docs.extend(d for d in op.documents if isinstance(d, dict))
        elif isinstance(op.update, dict):
            for key in ("$push", "$set"):
                inner = op.update.get(key)
                if isinstance(inner, dict):
                    docs.extend(v for v in inner.values() if isinstance(v, dict))
    for doc, values in zip(docs, returned, strict=False):
        for col, value in zip(columns, values, strict=True):
            field = layout.fields.get(col, col)
            converted = convert_value(
                value, plan["tables"][table]["columns"][col], env.options.uuid_as
            )
            if converted is None:
                doc.pop(field, None)
            else:
                doc[field] = converted


def _dml_verdicts(env: Env, query: Query) -> list[str]:
    """Foreign-key verdicts (not SAFE) involving the statement's target table."""
    from schemashift.optimizer.workload import write_target

    target = write_target(query)
    out: list[str] = []
    for v in env.result.equivalence.final.verdicts:
        if v.status == "SAFE":
            continue
        node = env.result.ir.by_id(v.node_id)
        parent = getattr(node, "parent", None)
        child = getattr(node, "child", None)
        if target in (parent, child) and v.rule_id.startswith(
            ("EQ-CASCADE", "EQ-SET", "EQ-RESTRICT", "EQ-REF")
        ):
            out.append(v.node_id)
    return out
