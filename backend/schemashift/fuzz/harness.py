"""Run fuzz cases through the real pipeline against live PostgreSQL and MongoDB sandboxes."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Literal

import psycopg
from pymongo.errors import PyMongoError

from schemashift.codegen import generate_code
from schemashift.codegen.layout import build_layouts
from schemashift.codegen.queries import translate_query
from schemashift.models.base import FrozenModel
from schemashift.models.schema import Schema
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification.ddl import UnsafeSqlError, ddl_statements
from schemashift.verification.diff import ResultDiff, RowDiff, compare
from schemashift.verification.runner import (
    Env,
    insert_seed,
    mongo_rows,
    pg_rows,
    run_python_source,
    safe_sql,
)
from schemashift.verification.sandbox import Sandbox, SandboxConfig
from schemashift.verification.seed import seed_from_inserts

Status = Literal["MATCH", "MISMATCH", "NOT_TRANSLATED", "PG_ERROR", "MONGO_ERROR", "SKIPPED"]


class InvalidCase(Exception):
    """The generated case is not usable (schema error, seed rejected by PostgreSQL)."""


class SelectOutcome(FrozenModel):
    sql: str
    status: Status
    pg: list[tuple[Any, ...]] = []
    mongo: list[tuple[Any, ...]] = []
    differing: list[RowDiff] = []
    hypothesis: str | None = None
    detail: str | None = None
    pipeline: list[dict[str, Any]] | None = None

    @property
    def comparable(self) -> bool:
        return self.status in ("MATCH", "MISMATCH")


class FuzzSession:
    """One sandbox loaded with a schema + seed. Use as a context manager."""

    def __init__(
        self,
        schema_sql: str,
        seed_sql: str,
        config: SandboxConfig,
        options: CompileOptions | None = None,
    ) -> None:
        self.schema_sql = schema_sql
        self.seed_sql = seed_sql
        self.config = config
        self.options = options or CompileOptions()
        self._sb: Sandbox | None = None
        self.env: Env | None = None
        self.untranslated: dict[str, int] = {}  # reason -> count (every untranslatable SELECT)

    @property
    def schema(self) -> Schema:
        assert self.env is not None
        return self.env.schema

    def __enter__(self) -> FuzzSession:
        result = compile_sql(self.schema_sql, "", self.seed_sql, self.options)
        if result.has_errors:
            raise InvalidCase("; ".join(d.message for d in result.diagnostics[:3]))
        codegen = generate_code(result, self.options, run_id="fuzz")
        layouts = build_layouts(result.graph, result.plan)
        statements, _ = ddl_statements(result.schema_)
        seed = seed_from_inserts(result.schema_, result.seed_queries, 0)
        sb = Sandbox(self.config)
        sb.create()
        self._sb = sb
        try:
            for stmt in statements:
                sb.pg.execute(stmt)
            insert_seed(sb.pg, result.schema_, seed)
        except psycopg.Error as exc:
            self.close()
            raise InvalidCase(str(exc).splitlines()[0]) from exc
        run_python_source(
            codegen.file("python/schema.py").content, "create_collections", sb.mongo_db
        )
        run_python_source(codegen.file("python/indexes.py").content, "create_indexes", sb.mongo_db)
        self.env = Env(result, codegen, layouts, sb, self.options)
        self.env.migrate()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._sb is not None:
            self._sb.close()
            self._sb = None

    def select(self, sql: str) -> SelectOutcome:
        """Run one SELECT on both sides and compare (differential testing)."""
        assert self.env is not None
        env = self.env
        parsed = parse(sql, "queries")
        queries = parsed.all_queries()
        if len(queries) != 1 or parsed.diagnostics:
            msg = "; ".join(d.message for d in parsed.diagnostics) or "not a single statement"
            return SelectOutcome(sql=sql, status="NOT_TRANSLATED", detail=f"parse: {msg}")
        query = queries[0]
        tq = translate_query(query, env.ctx)
        if tq.error:
            self.untranslated[tq.error] = self.untranslated.get(tq.error, 0) + 1
            return SelectOutcome(sql=sql, status="NOT_TRANSLATED", detail=tq.error)
        try:
            text = safe_sql(query, env.schema)
        except UnsafeSqlError as exc:
            return SelectOutcome(sql=sql, status="SKIPPED", detail=str(exc))
        try:
            left = pg_rows(env.sb.pg, text)
        except psycopg.Error as exc:
            return SelectOutcome(sql=sql, status="PG_ERROR", detail=str(exc).splitlines()[0])
        try:
            right = mongo_rows(env.sb.mongo_db, tq)
        except PyMongoError as exc:
            return SelectOutcome(
                sql=sql,
                status="MONGO_ERROR",
                pg=left,
                detail=str(exc).splitlines()[0],
                pipeline=tq.pipeline,
            )
        diff: ResultDiff = compare(left, right, tq.ordered, tq.order_positions, tq.global_aggregate)
        return SelectOutcome(
            sql=sql,
            status=diff.status,
            pg=left,
            mongo=right,
            differing=diff.differing,
            hypothesis=diff.hypothesis,
            pipeline=tq.pipeline,
        )
