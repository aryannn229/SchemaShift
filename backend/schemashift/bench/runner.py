"""Benchmark laboratory (Phase 20): PostgreSQL vs MongoDB under three placement plans.

I/O is allowed here and the module is only reachable from the CLI (``schemashift bench``),
never from the
web app. Correctness is established first (the same differential comparison the verifier uses);
only then
are queries timed, and the two results are reported in separate sections.
"""

from __future__ import annotations

import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from typing import Any

from pymongo.errors import PyMongoError

from schemashift.codegen import generate_code
from schemashift.codegen.layout import build_layouts
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query
from schemashift.optimizer.workload import flatten
from schemashift.pipeline import CompileOptions, CompileResult, compile_sql
from schemashift.verification.ddl import ddl_statements
from schemashift.verification.diff import compare
from schemashift.verification.runner import load_env, pg_rows, safe_sql
from schemashift.verification.sandbox import Sandbox, SandboxConfig
from schemashift.verification.seed import SeedData, generate_seed

PLANS = ("chosen", "all_reference", "all_embed")
MONGO_MAX_TIME_MS = 120_000


class Latency(FrozenModel):
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    throughput_qps: float
    reps: int


class QueryBench(FrozenModel):
    query_id: str
    sql: str
    status: str  # MATCH | MISMATCH | NOT_TRANSLATED | ERROR
    detail: str | None = None
    rows: int | None = None
    postgres: Latency | None = None
    mongo: Latency | None = None
    mongo_index_used: bool | None = None
    mongo_docs_examined: int | None = None
    postgres_index_used: bool | None = None
    rewrites: list[str] = []


class Storage(FrozenModel):
    postgres_data_bytes: int
    postgres_index_bytes: int
    mongo_data_bytes: int
    mongo_storage_bytes: int
    mongo_index_bytes: int


class PlanRun(FrozenModel):
    name: str
    decisions: dict[str, str]
    collections: list[str]
    load_seconds: float
    queries: list[QueryBench]
    storage: Storage
    correctness: float  # share of translated queries that returned the same rows


class BenchReport(FrozenModel):
    meta: dict[str, Any]
    plans: list[PlanRun]
    findings: list[str]


def percentile(sorted_ms: list[float], p: float) -> float:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_ms:
        return 0.0
    k = max(0, min(len(sorted_ms) - 1, math.ceil(p / 100 * len(sorted_ms)) - 1))
    return sorted_ms[k]


def latency(samples_s: list[float]) -> Latency:
    ms = sorted(s * 1000 for s in samples_s)
    total = sum(samples_s)
    return Latency(
        p50_ms=round(percentile(ms, 50), 3),
        p95_ms=round(percentile(ms, 95), 3),
        p99_ms=round(percentile(ms, 99), 3),
        mean_ms=round(statistics.fmean(ms), 3),
        throughput_qps=round(len(samples_s) / total, 2) if total else 0.0,
        reps=len(samples_s),
    )


def machine_info(pg: Any, mongo_db: Any) -> dict[str, Any]:
    import psycopg
    import pymongo

    with pg.cursor() as cur:
        cur.execute("SELECT version()")
        pg_version = str(cur.fetchone()[0])
    mongo_version = mongo_db.client.server_info().get("version")
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "postgres": pg_version.split(",")[0],
        "mongodb": mongo_version,
        "psycopg": psycopg.__version__,
        "pymongo": pymongo.version,
        "note": "both databases run in local Docker containers on the same machine",
    }


def _time(fn: Any, warmups: int, reps: int) -> list[float]:
    for _ in range(warmups):
        fn()
    out: list[float] = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def _plan_options(base: CompileResult, name: str, options: CompileOptions) -> CompileOptions:
    if name == "chosen":
        return options
    decision = "REFERENCE" if name == "all_reference" else "EMBED"
    overrides = {rid: decision for rid in base.plan.decisions}
    return options.model_copy(update={"relationship_overrides": overrides})


def _mongo_uses_index(db: Any, collection: str, pipeline: list[Any]) -> tuple[bool, int]:
    from schemashift.codegen.values import materialize

    plan = db.command(
        {
            "explain": {
                "aggregate": collection,
                "pipeline": materialize(pipeline),
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    )
    text = json.dumps(plan, default=str)
    from schemashift.verification.explain import observed_cost

    _, examined = observed_cost(json.loads(text))
    return ("IXSCAN" in text or "IDHACK" in text), examined


def _pg_uses_index(pg: Any, sql: str) -> bool:
    with pg.cursor() as cur:
        cur.execute("EXPLAIN (FORMAT JSON) " + sql)
        text = json.dumps(cur.fetchone()[0])
    return "Index" in text


def _storage(pg: Any, schema: str, db: Any) -> Storage:
    with pg.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(sum(pg_table_size(c.oid)), 0), "
            "COALESCE(sum(pg_indexes_size(c.oid)), 0) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'r'",
            (schema,),
        )
        data, index = cur.fetchone()
    stats = db.command("dbStats")
    return Storage(
        postgres_data_bytes=int(data),
        postgres_index_bytes=int(index),
        mongo_data_bytes=int(stats.get("dataSize", 0)),
        mongo_storage_bytes=int(stats.get("storageSize", 0)),
        mongo_index_bytes=int(stats.get("indexSize", 0)),
    )


def run_plan(
    name: str,
    schema_sql: str,
    queries_sql: str,
    options: CompileOptions,
    config: SandboxConfig,
    seed_data: SeedData,
    *,
    warmups: int,
    reps: int,
) -> tuple[PlanRun, dict[str, Any]]:
    base = compile_sql(schema_sql, queries_sql, "", options)
    opts = _plan_options(base, name, options)
    result = base if name == "chosen" else compile_sql(schema_sql, queries_sql, "", opts)
    codegen = generate_code(result, opts, run_id="bench")
    layouts = build_layouts(result.graph, result.plan)
    statements, _ = ddl_statements(result.schema_)
    translated = {t.query_id: t for t in codegen.queries}
    with Sandbox(config) as sb:
        started = time.monotonic()
        env, _rows, _migrated = load_env(result, opts, sb, codegen, layouts, statements, seed_data)
        load_seconds = time.monotonic() - started
        env_info = machine_info(sb.pg, sb.mongo_db)
        out: list[QueryBench] = []
        for q in flatten(result.queries):
            if q.kind != "SELECT":
                continue
            out.append(_bench_query(env, q, translated[q.id], warmups, reps))
        translatable = [x for x in out if x.status in ("MATCH", "MISMATCH")]
        correctness = (
            sum(x.status == "MATCH" for x in translatable) / len(translatable)
            if translatable
            else 1.0
        )
        run = PlanRun(
            name=name,
            decisions={rid: d.decision for rid, d in result.plan.decisions.items()},
            collections=list(codegen.collections),
            load_seconds=round(load_seconds, 2),
            queries=out,
            storage=_storage(sb.pg, sb.name, sb.mongo_db),
            correctness=round(correctness, 4),
        )
    return run, env_info


def _bench_query(env: Any, q: Query, tq: Any, warmups: int, reps: int) -> QueryBench:
    base = {"query_id": q.id, "sql": " ".join(q.raw_sql.split()), "rewrites": list(tq.rewrites)}
    if tq.error or tq.pipeline is None or tq.collection is None:
        return QueryBench(**base, status="NOT_TRANSLATED", detail=tq.error)
    sb = env.sb
    try:
        text = safe_sql(q, env.schema)
        left = pg_rows(sb.pg, text)
    except Exception as exc:  # noqa: BLE001 - reported per query
        return QueryBench(**base, status="ERROR", detail=f"postgres: {str(exc).splitlines()[0]}")
    from schemashift.codegen.values import materialize

    pipeline = materialize(tq.pipeline)
    coll = sb.mongo_db[tq.collection]

    def run_mongo() -> list[Any]:
        return list(coll.aggregate(pipeline, maxTimeMS=MONGO_MAX_TIME_MS))

    try:
        docs = run_mongo()
    except PyMongoError as exc:
        return QueryBench(**base, status="ERROR", detail=f"mongo: {str(exc).splitlines()[0]}")
    right = [tuple(d.get(c) for c in tq.output_columns) for d in docs]
    diff = compare(left, right, tq.ordered, tq.order_positions, tq.global_aggregate)
    status = diff.status
    pg_lat = mongo_lat = None
    ix_mongo = ix_pg = None
    examined = None
    if status == "MATCH":  # time only what is correct
        pg_lat = latency(_time(lambda: pg_rows(sb.pg, text), warmups, reps))
        mongo_lat = latency(_time(run_mongo, warmups, reps))
        ix_mongo, examined = _mongo_uses_index(sb.mongo_db, tq.collection, tq.pipeline)
        ix_pg = _pg_uses_index(sb.pg, text)
    return QueryBench(
        **base,
        status=status,
        detail=diff.hypothesis,
        rows=len(left),
        postgres=pg_lat,
        mongo=mongo_lat,
        mongo_index_used=ix_mongo,
        mongo_docs_examined=examined,
        postgres_index_used=ix_pg,
    )


def findings(plans: list[PlanRun]) -> list[str]:
    """Plain statements about who won, including where the chosen plan did not."""
    out: list[str] = []
    by = {p.name: p for p in plans}
    chosen = by.get("chosen")
    if chosen is None:
        return out
    for cq in chosen.queries:
        if cq.mongo is None:
            out.append(
                f"{cq.query_id}: not timed ({cq.status}{': ' + cq.detail if cq.detail else ''})"
            )
            continue
        times: dict[str, float] = {"chosen": cq.mongo.p50_ms}
        for other in ("all_reference", "all_embed"):
            oq = (
                next((x for x in by[other].queries if x.query_id == cq.query_id), None)
                if other in by
                else None
            )
            if oq is not None and oq.mongo is not None:
                times[other] = oq.mongo.p50_ms
        best = min(times, key=lambda k: times[k])
        pg = cq.postgres.p50_ms if cq.postgres else None
        line = f"{cq.query_id}: MongoDB p50 " + ", ".join(
            f"{k} {v:.2f} ms" for k, v in times.items()
        )
        if pg is not None:
            line += f"; PostgreSQL {pg:.2f} ms"
        line += (
            "; chosen plan is the fastest" if best == "chosen" else f"; {best} beat the chosen plan"
        )
        out.append(line)
    return out


def run_bench(
    schema_sql: str,
    queries_sql: str,
    config: SandboxConfig,
    *,
    scale: int,
    seed: int = 0,
    warmups: int = 3,
    reps: int = 20,
    options: CompileOptions | None = None,
    plans: tuple[str, ...] = PLANS,
    log: Any = None,
) -> BenchReport:
    options = options or CompileOptions()
    first = compile_sql(schema_sql, queries_sql, "", options)
    if first.has_errors:
        raise ValueError("fix the schema errors before benchmarking")
    t = time.monotonic()
    seed_data = generate_seed(first.schema_, seed, scale, cap=max(scale, 1))
    if log:
        log(f"seed data generated in {time.monotonic() - t:.1f}s")
    runs: list[PlanRun] = []
    info: dict[str, Any] = {}
    for name in plans:
        if log:
            log(f"plan {name} ...")
        run, info = run_plan(
            name, schema_sql, queries_sql, options, config, seed_data, warmups=warmups, reps=reps
        )
        runs.append(run)
    meta = {
        **info,
        "scale_rows_per_table": scale,
        "seed": seed,
        "warmups": warmups,
        "reps": reps,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": {t: len(r) for t, r in seed_data.rows.items()},
    }
    return BenchReport(meta=meta, plans=runs, findings=findings(runs))
