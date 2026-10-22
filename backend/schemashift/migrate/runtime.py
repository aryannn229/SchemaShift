"""Safe migration mode (Phase 23): dry run, pre-validation, checkpoints, manifest, consistency
checks.

I/O is allowed here. A full migration copies PostgreSQL tables into MongoDB in keyset-paged
batches; every
batch is upserted by ``_id`` (so a repeated batch is harmless) and the manifest is rewritten
atomically
afterwards (so a crash leaves the previous checkpoint). ``resume`` continues from the persisted
cursor.
Records that would violate the target validators are quarantined, never dropped silently.
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path
from typing import Any

import bson
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from schemashift.codegen.collections import CollectionSpec, build_collections
from schemashift.codegen.indexes import build_indexes
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.codegen.migration import migration_plan
from schemashift.codegen.runtime_migrate import build_documents, write_batch
from schemashift.codegen.values import materialize
from schemashift.migrate import manifest as mf
from schemashift.models.base import FrozenModel
from schemashift.pipeline import CompileOptions, CompileResult

QUARANTINE = "_schemashift_quarantine"
SNAPSHOT_PREFIX = "_snapshot_"
CRASH_ENV = "SCHEMASHIFT_CRASH_AFTER_BATCHES"  # fault injection for the resume test (os._exit)


class MigrationError(Exception):
    """A refusal or failure with an actionable message."""


class LossyStepsNeedConsent(MigrationError):
    """An irreversible step was requested without ``--i-understand-this-is-lossy``."""


class CheckResult(FrozenModel):
    name: str
    ok: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""


class DryRunReport(FrozenModel):
    tables: dict[str, int]  # source rows per table
    documents: dict[str, int]  # estimated documents per collection
    sample_documents: int
    avg_document_bytes: int
    estimated_storage_bytes: int
    docs_per_second: float
    estimated_seconds: float
    invalid_in_sample: int
    invalid_total_estimate: int
    problems: list[dict[str, Any]]  # records that would fail, with reasons
    writes_performed: int = 0  # always 0 for a dry run


class Job:
    """Everything a migration needs: the compiled schema, both connections and the state
    directory."""

    def __init__(
        self,
        result: CompileResult,
        options: CompileOptions,
        pg_dsn: str,
        mongo_url: str,
        database: str,
        state_dir: Path,
        pg_schema: str = "public",
        batch_size: int = 500,
        prevalidate: bool = True,
    ) -> None:
        self.result, self.options = result, options
        self.pg_dsn, self.mongo_url, self.database = pg_dsn, mongo_url, database
        self.state_dir, self.pg_schema = state_dir, pg_schema
        self.batch_size, self.prevalidate = batch_size, prevalidate
        self.layouts: dict[str, TableLayout] = build_layouts(result.graph, result.plan)
        self.plan = migration_plan(result.schema_, self.layouts, options)
        self.specs: dict[str, CollectionSpec] = {
            c.name: c
            for c in build_collections(result.schema_, self.layouts, options.uuid_as).collections
        }
        self.conn: Any = None
        self.client: Any = None
        self.db: Any = None

    # -- connections ---------------------------------------------------------------------------
    def __enter__(self) -> Job:
        self.conn = psycopg.connect(self.pg_dsn, autocommit=True)
        self.client = MongoClient(self.mongo_url, serverSelectionTimeoutMS=5000)
        self.db = self.client[self.database]
        return self

    def __exit__(self, *exc: object) -> None:
        if self.conn is not None:
            self.conn.close()
        if self.client is not None:
            self.client.close()

    # -- helpers -------------------------------------------------------------------------------
    def ident(self, table: str) -> sql.Composed:
        return sql.SQL(".").join([sql.Identifier(self.pg_schema), sql.Identifier(table)])

    def count_rows(self, table: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(self.ident(table)))
            return int(cur.fetchone()[0])

    def key_columns(self, table: str) -> list[str]:
        t = self.plan["tables"][table]
        return list(t["pk"])

    def fetch_batch(
        self, table: str, cursor: list[Any] | None, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], list[Any] | None, int]:
        keys = self.key_columns(table)
        cols = keys or list(self.plan["tables"][table]["columns"])
        order = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
        with self.conn.cursor(row_factory=dict_row) as cur:
            if keys and cursor is not None:
                where = sql.SQL("({}) > ({})").format(
                    order, sql.SQL(", ").join(sql.Placeholder() for _ in keys)
                )
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE {} ORDER BY {} LIMIT %s").format(
                        self.ident(table), where, order
                    ),
                    [*cursor, limit],
                )
            elif keys:
                cur.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY {} LIMIT %s").format(
                        self.ident(table), order
                    ),
                    [limit],
                )
            else:
                cur.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY {} LIMIT %s OFFSET %s").format(
                        self.ident(table), order
                    ),
                    [limit, offset],
                )
            rows = list(cur.fetchall())
        next_cursor = [str(rows[-1][k]) for k in keys] if rows and keys else cursor
        return rows, next_cursor, offset + len(rows)

    def docs(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return build_documents(self.conn, self.plan, table, rows, self.pg_schema)

    # -- validation against the real validator ----------------------------------------------------
    def invalid_docs(self, collection: str, docs: list[dict[str, Any]]) -> dict[int, list[str]]:
        """Indexes of ``docs`` the collection's validator would reject, with reasons.

        Uses ``$documents`` + ``$match`` on the server, so nothing is written and the semantics
        are MongoDB's.
        """
        if not docs:
            return {}
        validator = materialize(self.specs[collection].validator)
        bad: dict[int, list[str]] = {}
        try:
            out = self.db.aggregate([{"$documents": docs}, {"$match": {"$nor": [validator]}}])
            failing = [d["_id"] for d in out]
        except PyMongoError as exc:
            raise MigrationError(f"validator check failed on the server: {exc}") from exc
        if failing:
            pending = list(failing)
            for i, doc in enumerate(docs):
                if doc["_id"] in pending:
                    pending.remove(doc["_id"])
                    bad[i] = self.explain(validator, doc)
        return bad

    def _passes(self, clause: dict[str, Any], doc: Any) -> bool:
        try:
            return bool(list(self.db.aggregate([{"$documents": [doc]}, {"$match": clause}])))
        except PyMongoError:
            return True  # cannot test this clause: do not blame it

    def explain(self, validator: dict[str, Any], doc: dict[str, Any]) -> list[str]:
        """Which clause of the validator the document breaks, down to the offending nested field."""
        reasons: list[str] = []
        for part in validator.get("$and") or [validator]:
            if "$jsonSchema" in part:
                self._explain_schema(part["$jsonSchema"], doc, "", reasons)
            elif "$expr" in part and not self._passes(part, doc):
                reasons.append("a CHECK constraint is violated")
        return reasons[:5] or ["the validator rejects the document"]

    def _explain_schema(
        self, schema: dict[str, Any], doc: dict[str, Any], path: str, out: list[str]
    ) -> None:
        for field in schema.get("required", []):
            if field not in doc:
                out.append(f"required field {path}{field} is missing")
        props: dict[str, Any] = schema.get("properties", {})
        for field, value in doc.items():
            sub = props.get(field)
            if sub is None:
                if schema.get("additionalProperties") is False:
                    out.append(f"field {path}{field} is not in the schema")
                continue
            if self._passes({"$jsonSchema": {"properties": {field: sub}}}, {field: value}):
                continue
            items = sub.get("items")
            if (
                isinstance(value, list)
                and isinstance(items, dict)
                and items.get("bsonType") == "object"
            ):
                for n, element in enumerate(value):
                    if isinstance(element, dict) and not self._passes(
                        {"$jsonSchema": items}, element
                    ):
                        self._explain_schema(items, element, f"{path}{field}[{n}].", out)
            elif isinstance(value, dict) and sub.get("bsonType") == "object":
                self._explain_schema(sub, value, f"{path}{field}.", out)
            else:
                out.append(f"field {path}{field} does not match {self._brief(sub)}")

    @staticmethod
    def _brief(sub: dict[str, Any]) -> str:
        return ", ".join(
            f"{k}={v}"
            for k, v in sub.items()
            if k in ("bsonType", "minimum", "maximum", "enum", "maxLength", "minLength")
        )[:80]

    # -- document size / estimates
    # ------------------------------------------------------------------
    @staticmethod
    def doc_bytes(doc: dict[str, Any]) -> int:
        return len(bson.encode(doc))


# =================================================================================== operations
def prepare_target(job: Job) -> None:
    """Create collections (with validators) and indexes if they do not exist yet."""
    existing = set(job.db.list_collection_names())
    for spec in job.specs.values():
        if spec.name not in existing:
            job.db.create_collection(
                spec.name,
                validator=materialize(spec.validator),
                validationLevel=spec.validation_level,
                validationAction=spec.validation_action,
            )
    for ix in build_indexes(job.result.schema_, job.result.graph, job.layouts):
        kwargs: dict[str, Any] = {"name": ix.name}
        if ix.unique:
            kwargs["unique"] = True
        if ix.partial_filter:
            kwargs["partialFilterExpression"] = materialize(ix.partial_filter)
        job.db[ix.collection].create_index(list(ix.keys), **kwargs)


def new_manifest(job: Job) -> mf.Manifest:
    roots = job.plan["roots"]
    counts = {t: job.count_rows(t) for t in job.plan["tables"]}
    return mf.Manifest(
        kind="full",
        database=job.database,
        batch_size=job.batch_size,
        tables=[
            mf.TableProgress(
                table=t,
                collection=job.plan["tables"][t]["collection"],
                total_rows=counts[t],
            )
            for t in roots
        ],
        source_fingerprint=counts,
    )


def dry_run(job: Job, sample_batches: int = 3) -> DryRunReport:
    """Report what would change without writing to the target (reads only; validation is
    server-side)."""
    counts = {t: job.count_rows(t) for t in job.plan["tables"]}
    started = time.perf_counter()
    docs_seen = 0
    sizes = 0
    invalid = 0
    sampled_rows = 0
    problems: list[dict[str, Any]] = []
    total_rows = 0
    for table in job.plan["roots"]:
        coll = job.plan["tables"][table]["collection"]
        total_rows += counts[table]
        cursor, offset = None, 0
        for _ in range(sample_batches):
            rows, cursor, offset = job.fetch_batch(table, cursor, offset, job.batch_size)
            if not rows:
                break
            docs = job.docs(table, rows)
            docs_seen += len(docs)
            sampled_rows += len(rows)
            sizes += sum(job.doc_bytes(d) for d in docs)
            bad = job.invalid_docs(coll, docs) if job.prevalidate else {}
            invalid += len(bad)
            for i, reasons in bad.items():
                if len(problems) < 50:
                    problems.append(
                        {"collection": coll, "key": str(docs[i].get("_id")), "reasons": reasons}
                    )
    elapsed = max(time.perf_counter() - started, 1e-6)
    rate = docs_seen / elapsed if docs_seen else 0.0
    est_docs = {job.plan["tables"][t]["collection"]: counts[t] for t in job.plan["roots"]}
    avg = int(sizes / docs_seen) if docs_seen else 0
    total_docs = sum(est_docs.values())
    return DryRunReport(
        tables=counts,
        documents=est_docs,
        sample_documents=docs_seen,
        avg_document_bytes=avg,
        estimated_storage_bytes=avg * total_docs,
        docs_per_second=round(rate, 1),
        estimated_seconds=round(total_docs / rate, 1) if rate else 0.0,
        invalid_in_sample=invalid,
        invalid_total_estimate=int(invalid / docs_seen * total_docs) if docs_seen else 0,
        problems=problems,
    )


def _maybe_crash(batches_done: int) -> None:
    limit = os.environ.get(CRASH_ENV)
    if limit and batches_done >= int(limit):
        os._exit(137)  # a real kill: no cleanup, no finally blocks, no manifest flush


def run(job: Job, manifest: mf.Manifest | None = None) -> mf.Manifest:
    """Run (or resume) a full migration. Idempotent; safe to call again after any crash."""
    state_dir = job.state_dir
    m = manifest or mf.load(state_dir)
    if m is None:
        m = new_manifest(job)
        job_note = "started"
    else:
        m.resumed += 1
        job_note = f"resumed (attempt {m.resumed + 1})"
        now = {t: job.count_rows(t) for t in job.plan["tables"]}
        if now != m.source_fingerprint:
            m.action(
                "the source row counts changed since the run started "
                f"({m.source_fingerprint} -> {now}); rows added or removed in finished tables "
                "are not re-read"
            )
    m.action(job_note)
    m.state = "running"
    m.error = None
    mf.save(m, state_dir)
    try:
        prepare_target(job)
        batches_total = 0
        for prog in m.tables:
            if prog.state == "done":
                continue
            prog.state = "running"
            while True:
                rows, cursor, offset = job.fetch_batch(
                    prog.table, prog.cursor, prog.offset, m.batch_size
                )
                if not rows:
                    break
                docs = job.docs(prog.table, rows)
                bad = job.invalid_docs(prog.collection, docs) if job.prevalidate else {}
                good = [d for i, d in enumerate(docs) if i not in bad]
                if bad:
                    _quarantine(job, m, prog, docs, bad)
                try:
                    write_batch(job.db, prog.collection, good)
                except PyMongoError as exc:
                    raise MigrationError(f"{prog.collection}: write failed: {exc}") from exc
                prog.written += len(good)
                prog.batches += 1
                prog.cursor, prog.offset = cursor, offset
                mf.save(m, state_dir)  # checkpoint: after the batch is durable in MongoDB
                batches_total += 1
                _maybe_crash(batches_total)
            prog.state = "done"
            mf.save(m, state_dir)
        if job.options.preserve_integer_ids:
            from schemashift.codegen.runtime_migrate import _seed_counters

            _seed_counters(job.conn, job.db, job.plan, job.pg_schema)
        m.state = "completed"
        m.action("completed")
    except BaseException as exc:
        m.state = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        m.error = f"{type(exc).__name__}: {exc}"[:500]
        mf.save(m, state_dir)
        raise
    mf.save(m, state_dir)
    return m


def _quarantine(
    job: Job,
    m: mf.Manifest,
    prog: mf.TableProgress,
    docs: list[dict[str, Any]],
    bad: dict[int, list[str]],
) -> None:
    entries = []
    for i, reasons in bad.items():
        key = docs[i].get("_id")
        entries.append(
            {
                "run": m.id,
                "collection": prog.collection,
                "key": key,
                "reasons": reasons,
                "doc": docs[i],
            }
        )
        m.quarantine_total += 1
        if len(m.quarantined) < 1000:
            m.quarantined.append(
                mf.QuarantinedRecord(collection=prog.collection, key=str(key), reasons=reasons)
            )
    from pymongo import ReplaceOne

    job.db[QUARANTINE].bulk_write(
        [
            ReplaceOne(
                {"run": e["run"], "collection": e["collection"], "key": e["key"]}, e, upsert=True
            )
            for e in entries
        ],
        ordered=False,
    )
    prog.quarantined += len(entries)
    m.action(
        f"quarantined {len(entries)} record(s) of {prog.collection} "
        f"(kept in {QUARANTINE}, not written)"
    )


# =================================================================================== verification
def verify(
    job: Job, manifest: mf.Manifest | None = None, sample: int = 50, seed: int = 0
) -> list[CheckResult]:
    """Post-migration consistency checks. The result is also stored in the manifest."""
    m = manifest or mf.load(job.state_dir)
    quarantined: dict[str, int] = {}
    for prog in m.tables if m else []:
        quarantined[prog.collection] = prog.quarantined
    out: list[CheckResult] = []
    for table in job.plan["roots"]:
        t = job.plan["tables"][table]
        coll = t["collection"]
        pg = job.count_rows(table)
        mongo = job.db[coll].count_documents({})
        q = quarantined.get(coll, 0)
        out.append(
            CheckResult(
                name=f"document count {coll}",
                ok=mongo + q == pg,
                expected=pg - q,
                actual=mongo,
                detail=f"{pg} rows, {q} quarantined",
            )
        )
        out.append(_key_coverage(job, table, coll, q))
        out.extend(_embedded_counts(job, table, coll, m))
    out.extend(_reference_coverage(job))
    out.extend(_sampled_comparison(job, sample, seed))
    if m is not None:
        m.checks = [c.model_dump() for c in out]
        mf.save(m, job.state_dir)
    return out


def _key_coverage(job: Job, table: str, coll: str, quarantined: int) -> CheckResult:
    keys = job.key_columns(table)
    if len(keys) != 1 or job.plan["tables"][table]["id_column"] is None:
        return CheckResult(
            name=f"key coverage {coll}",
            ok=True,
            detail="no single-column key: covered by the count",
        )
    missing = 0
    cursor: list[Any] | None = None
    while True:
        rows, cursor, _ = job.fetch_batch(table, cursor, 0, 1000)
        if not rows:
            break
        ids = [r[keys[0]] for r in rows]
        found = {d["_id"] for d in job.db[coll].find({"_id": {"$in": ids}}, {"_id": 1})}
        missing += sum(1 for i in ids if i not in found)
    return CheckResult(
        name=f"key coverage {coll}",
        ok=missing == quarantined,
        expected=quarantined,
        actual=missing,
        detail="primary keys with no document (must equal the quarantined records)",
    )


def _embedded_counts(job: Job, table: str, coll: str, m: mf.Manifest | None) -> list[CheckResult]:
    out: list[CheckResult] = []
    for name, t in job.plan["tables"].items():
        if t["collection"] != coll or t["kind"] not in ("array", "ref_docs"):
            continue
        path = t["path"]
        stages: list[dict[str, Any]] = []
        prefix = ""
        for seg in path:
            prefix = f"{prefix}.{seg}" if prefix else seg
            stages.append({"$unwind": f"${prefix}"})
        stages.append({"$count": "n"})
        got = list(job.db[coll].aggregate(stages))
        actual = got[0]["n"] if got else 0
        expected = job.count_rows(name)
        # children of quarantined parents are not in MongoDB: only report when nothing was
        # quarantined
        skip = m is not None and any(q.quarantined for q in m.tables if q.collection == coll)
        out.append(
            CheckResult(
                name=f"embedded rows {name} in {coll}.{'.'.join(path)}",
                ok=True if skip else actual == expected,
                expected=expected,
                actual=actual,
                detail="skipped: parents were quarantined"
                if skip
                else "array elements vs child rows",
            )
        )
    return out


def _reference_coverage(job: Job) -> list[CheckResult]:
    out: list[CheckResult] = []
    for rel in job.result.graph.relationships.values():
        child, parent = job.layouts[rel.child], job.layouts[rel.parent]
        if not (child.is_root and parent.is_root) or rel.is_self_reference:
            continue
        if len(rel.fk.columns) != 1 or parent.id_column != rel.fk.ref_columns[0]:
            continue
        field = child.fields.get(rel.fk.columns[0])
        if field is None:
            continue
        pipeline = [
            {"$match": {field: {"$ne": None}}},
            {
                "$lookup": {
                    "from": parent.collection,
                    "localField": field,
                    "foreignField": "_id",
                    "as": "_p",
                }
            },
            {"$match": {"_p": {"$size": 0}}},
            {"$count": "n"},
        ]
        got = list(job.db[child.collection].aggregate(pipeline))
        orphans = got[0]["n"] if got else 0
        out.append(
            CheckResult(
                name=f"reference coverage {rel.id}",
                ok=orphans == 0,
                expected=0,
                actual=orphans,
                detail=f"{child.collection}.{field} values with no {parent.collection} document",
            )
        )
    return out


def _sampled_comparison(job: Job, sample: int, seed: int) -> list[CheckResult]:
    rng = random.Random(seed)
    out: list[CheckResult] = []
    for table in job.plan["roots"]:
        coll = job.plan["tables"][table]["collection"]
        total = job.count_rows(table)
        if not total:
            continue
        picks = sorted(rng.sample(range(total), min(sample, total)))
        keys = job.key_columns(table) or list(job.plan["tables"][table]["columns"])
        order = sql.SQL(", ").join(sql.Identifier(c) for c in keys)
        rows: list[dict[str, Any]] = []
        with job.conn.cursor(row_factory=dict_row) as cur:
            for off in picks:
                cur.execute(
                    sql.SQL("SELECT * FROM {} ORDER BY {} LIMIT 1 OFFSET %s").format(
                        job.ident(table), order
                    ),
                    [off],
                )
                row = cur.fetchone()
                if row:
                    rows.append(row)
        expected = job.docs(table, rows)
        mismatched = 0
        absent = 0
        for doc in expected:
            stored = job.db[coll].find_one({"_id": doc["_id"]})
            if stored is None:
                absent += 1  # quarantined or missing: reported by the count and key checks
            elif stored != doc:
                mismatched += 1
        out.append(
            CheckResult(
                name=f"sampled deep comparison {coll}",
                ok=mismatched == 0,
                expected=0,
                actual=mismatched,
                detail=(
                    f"{len(expected)} rows rebuilt from PostgreSQL and compared field by field; "
                    f"{absent} not in MongoDB"
                ),
            )
        )
    return out


def status(state_dir: Path) -> dict[str, Any]:
    m = mf.load(state_dir)
    if m is None:
        return {"state": "none"}
    return {
        "id": m.id,
        "state": m.state,
        "kind": m.kind,
        "resumed": m.resumed,
        "tables": {
            t.table: f"{t.state} {t.written}/{t.total_rows} (quarantined {t.quarantined})"
            for t in m.tables
        },
        "steps": {s.id: s.state for s in m.steps},
        "quarantined": m.quarantine_total,
        "error": m.error,
        "checks": [f"{'ok' if c['ok'] else 'FAIL'} {c['name']}" for c in m.checks],
    }


def plan_preview(job: Job) -> dict[str, Any]:
    """What `run` would do, in order (no connections to the target needed beyond reading counts)."""
    return {
        "database": job.database,
        "collections": {
            job.plan["tables"][t]["collection"]: {
                "source_rows": job.count_rows(t),
                "embeds": sorted(n for n, x in job.plan["tables"].items() if x["host"] == t),
            }
            for t in job.plan["roots"]
        },
        "batch_size": job.batch_size,
        "steps": [
            "create collections with validators and indexes (skipped when they exist)",
            "copy each root table in primary-key order, one batch at a time; checkpoint per batch",
            "quarantine records the validators would reject (kept in _schemashift_quarantine)",
            "consistency checks: counts, keys, references, embedded rows, sampled comparison",
        ],
    }


# =================================================================================== incremental
# jobs
def run_script(
    job_db: Any,
    ns: dict[str, Any],
    state_dir: Path,
    *,
    database: str,
    consent_lossy: bool = False,
    log: Any = print,
) -> mf.Manifest:
    """Run (or resume) a generated incremental migration (``evolve.py``) with a manifest and
    snapshots.

    Reversible steps run directly. Before a step that cannot be undone, the affected collection is
    copied
    to ``_snapshot_<run>_<collection>`` and recorded; without ``consent_lossy`` such steps are
    refused.
    """
    m = mf.load(state_dir)
    if m is None or m.kind != "incremental":
        m = mf.Manifest(
            kind="incremental",
            database=database,
            steps=[
                mf.ScriptStep(
                    id=s["id"], description=s["description"], reversibility=s["reversibility"]
                )
                for s in ns["STEPS"]
            ],
        )
    else:
        m.resumed += 1
    m.state = "running"
    mf.save(m, state_dir)
    try:
        for meta in ns["STEPS"]:
            st = m.step(meta["id"])
            if st.state == "done":
                continue
            if meta["manual"] or meta["reversibility"] == "IRREVERSIBLE":
                if not consent_lossy:
                    raise LossyStepsNeedConsent(
                        f"{meta['id']} ({meta['description']}) is irreversible: {meta['recovery']} "
                        "Re-run with --i-understand-this-is-lossy after reading the recovery "
                        "strategy."
                    )
                if meta["collection"]:
                    snap = f"{SNAPSHOT_PREFIX}{m.id}_{meta['collection']}"
                    job_db[meta["collection"]].aggregate([{"$match": {}}, {"$out": snap}]).close()
                    st.snapshot = snap
                    m.action(
                        f"snapshot of {meta['collection']} saved as {snap} before {meta['id']}"
                    )
                if meta["manual"]:
                    st.state = "skipped"
                    m.action(f"{meta['id']} is manual: {meta['description']} (not executed)")
                    mf.save(m, state_dir)
                    continue
            st.state = "running"
            mf.save(m, state_dir)
            log(f"APPLY {meta['id']} {meta['description']}")
            ns["APPLY"][meta["id"]](job_db)
            st.state = "done"
            mf.save(m, state_dir)
            _maybe_crash_step(sum(s.state == "done" for s in m.steps))
        m.state = "completed"
        m.action("completed")
    except BaseException as exc:
        m.state = "failed"
        m.error = f"{type(exc).__name__}: {exc}"[:500]
        mf.save(m, state_dir)
        raise
    mf.save(m, state_dir)
    return m


def _maybe_crash_step(done: int) -> None:
    limit = os.environ.get("SCHEMASHIFT_CRASH_AFTER_STEPS")
    if limit and done >= int(limit):
        os._exit(137)


def rollback_script(
    job_db: Any,
    ns: dict[str, Any],
    state_dir: Path,
    *,
    consent_lossy: bool = False,
    log: Any = print,
) -> mf.Manifest:
    """Undo a script run: reversible steps through their rollback code, irreversible ones from
    snapshots."""
    m = mf.load(state_dir)
    if m is None:
        raise MigrationError("no manifest in the state directory: nothing to roll back")
    for meta in reversed(ns["STEPS"]):
        st = m.step(meta["id"])
        if st.state not in ("done", "running"):
            continue
        fn = ns["ROLLBACK"].get(meta["id"])
        if fn is not None:
            log(f"UNDO  {meta['id']} {meta['description']}")
            fn(job_db)
            st.state = "rolled_back"
        elif st.snapshot:
            if not consent_lossy:
                raise LossyStepsNeedConsent(
                    f"{meta['id']} can only be undone by restoring snapshot {st.snapshot} "
                    "(anything written after the snapshot is lost); "
                    "use --i-understand-this-is-lossy"
                )
            coll = meta["collection"]
            job_db[st.snapshot].rename(coll, dropTarget=True)
            st.state = "rolled_back"
            m.action(f"restored {coll} from {st.snapshot}")
        else:
            m.action(f"{meta['id']} had no rollback and no snapshot: nothing restored")
        mf.save(m, state_dir)
    m.state = "rolled_back"
    mf.save(m, state_dir)
    return m
