"""Verification harness against real PostgreSQL and MongoDB."""

from __future__ import annotations

import time
from typing import Any

import psycopg
import pytest

from schemashift.metrics.evaluate import DEFAULT_CORPUS, load_corpus
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification import (
    Sandbox,
    SandboxConfig,
    VerificationError,
    sweep,
    verify,
)
from schemashift.verification.ddl import UnsafeSqlError
from schemashift.verification.runner import safe_sql
from tests.conftest import MONGO_URL, PG_DSN
from tests.unit.codegen.helpers import SAMPLE_NAMES, sample

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def config(pg_dsn: str, mongo_client: Any) -> SandboxConfig:
    return SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL, statement_timeout_ms=3000)


def leftovers(config: SandboxConfig) -> dict[str, list[str]]:
    """Sandboxes that still exist (sweep with a time machine drops everything)."""
    return sweep(config, now=time.time() + 10 * 3600)


# --------------------------------------------------------------------- sandbox
def test_sandbox_lifecycle_creates_and_drops_everything(
    config: SandboxConfig, mongo_client: Any
) -> None:
    with Sandbox(config) as sb:
        sb.pg.execute("CREATE TABLE t (a INT)")
        sb.pg.execute("INSERT INTO t VALUES (1)")
        assert sb.pg.execute("SELECT current_schema()").fetchone()[0] == sb.name
        sb.mongo_db["c"].insert_one({"a": 1})
        assert sb.name in mongo_client.list_database_names()
        name = sb.name
    with psycopg.connect(PG_DSN) as admin:
        assert (
            admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is None
        )
        assert (
            admin.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone()
            is None
        )
    assert name not in mongo_client.list_database_names()


def test_sandbox_is_dropped_even_when_the_body_raises(
    config: SandboxConfig, mongo_client: Any
) -> None:
    holder: dict[str, str] = {}
    with pytest.raises(RuntimeError), Sandbox(config) as sb:
        holder["name"] = sb.name
        raise RuntimeError("boom")
    assert holder["name"] not in mongo_client.list_database_names()
    assert leftovers(config) == {"postgres": [], "mongo": []}


def test_sandbox_role_is_restricted(config: SandboxConfig) -> None:
    with Sandbox(config) as sb:
        flags = sb.pg.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolconnlimit FROM pg_roles WHERE rolname = %s",
            (sb.name,),
        ).fetchone()
        assert (
            tuple(flags)[:4] == (False, False, False, False) and flags[4] == config.connection_limit
        )
        for forbidden in (
            "CREATE TABLE public.escape (a INT)",
            "CREATE ROLE evil LOGIN",
            "CREATE DATABASE evil",
            "SELECT * FROM pg_shadow",
            "COPY (SELECT 1) TO PROGRAM 'echo hi'",
            "CREATE EXTENSION dblink",
        ):
            with pytest.raises(psycopg.Error):
                sb.pg.execute(forbidden)


def test_sandboxes_cannot_see_each_other(config: SandboxConfig) -> None:
    with Sandbox(config) as one, Sandbox(config) as two:
        one.pg.execute("CREATE TABLE secret (a INT)")
        with pytest.raises(psycopg.Error):
            two.pg.execute(f'SELECT * FROM "{one.name}".secret')
        with pytest.raises(psycopg.errors.UndefinedTable):
            two.pg.execute("SELECT * FROM secret")


def test_statement_timeout_is_enforced(pg_dsn: str, mongo_client: Any) -> None:
    quick = SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL, statement_timeout_ms=400)
    with Sandbox(quick) as sb:
        started = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            sb.pg.execute("SELECT pg_sleep(10)")
        assert time.monotonic() - started < 5


def test_connection_pool_is_capped(pg_dsn: str, mongo_client: Any) -> None:
    from psycopg.conninfo import make_conninfo

    small = SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL, connection_limit=2)
    with Sandbox(small) as sb:
        extra = psycopg.connect(make_conninfo(PG_DSN, user=sb.name, password=sb._password))
        try:
            with pytest.raises(psycopg.OperationalError, match="too many connections"):
                psycopg.connect(make_conninfo(PG_DSN, user=sb.name, password=sb._password))
        finally:
            extra.close()


def test_janitor_removes_only_stale_sandboxes(config: SandboxConfig, mongo_client: Any) -> None:
    stale, fresh = Sandbox(config), Sandbox(config)
    stale.create()
    fresh.create()
    try:
        assert sweep(config, now=time.time()) == {"postgres": [], "mongo": []}  # nothing is old yet
        future = time.time() + config.max_age_seconds + 60
        # only `stale` should go when `fresh` is made to look recent: rewrite its markers
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(
                f"COMMENT ON SCHEMA \"{fresh.name}\" IS 'schemashift-sandbox:{int(future)}'"
            )
        fresh.mongo_db["_sandbox"].update_one({"_id": "marker"}, {"$set": {"created_at": future}})
        dropped = sweep(config, now=future)
        assert stale.name in dropped["postgres"] and stale.name in dropped["mongo"]
        assert fresh.name not in dropped["postgres"] and fresh.name not in dropped["mongo"]
        assert fresh.pg.execute("SELECT 1").fetchone()[0] == 1
    finally:
        stale.close()
        fresh.close()
    assert leftovers(config) == {"postgres": [], "mongo": []}


def test_janitor_drops_orphan_roles(config: SandboxConfig) -> None:
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute('CREATE ROLE "run_abcdef012345" LOGIN')
    assert sweep(config)["postgres"] == ["run_abcdef012345"]


# ----------------------------------------------------------------------- runner
@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_samples_verify_with_high_correctness(name: str, config: SandboxConfig) -> None:
    schema_sql, queries_sql, options = sample(name)
    result = compile_sql(schema_sql, queries_sql, "", options)
    report = verify(result, options, config, seed=7)
    assert report.verified, "queries were verified"
    assert report.correctness >= 0.95, [o for o in report.queries if o.status != "MATCH"]
    assert report.unexplained == []
    assert all(o.status in ("MATCH", "MISMATCH") for o in report.queries), report.queries
    assert report.migrated and sum(report.rows.values()) > 100
    assert leftovers(config) == {"postgres": [], "mongo": []}


def test_banking_probes_show_orphaned_transactions(config: SandboxConfig) -> None:
    schema_sql, queries_sql, options = sample("banking")
    report = verify(compile_sql(schema_sql, queries_sql, "", options), options, config, seed=2)
    probe = next(
        p
        for p in report.probes
        if p.verdict_id == "cascade_delete:transactions.account_id->accounts.id"
    )
    assert (
        probe.status == "BROKEN" and probe.demonstrates and "transactions" in probe.tables_differing
    )
    assert "statement succeeded" in probe.postgres and "no cascade" in probe.mongo
    restrict = next(p for p in report.probes if p.rule_id == "EQ-RESTRICT")
    assert "REJECTED" in restrict.postgres and restrict.demonstrates
    orphan = next(p for p in report.probes if p.rule_id == "EQ-REF-INTEGRITY")
    assert "REJECTED" in orphan.postgres and "orphan" in orphan.mongo


def test_probes_leave_both_databases_unchanged(config: SandboxConfig) -> None:
    schema_sql, queries_sql, options = sample("ecommerce")
    result = compile_sql(schema_sql, "", "", options)
    first = verify(result, options, config, seed=3, run_probes=True)
    second = verify(result, options, config, seed=3, run_probes=False)
    assert first.rows == second.rows and first.migrated == second.migrated


def test_same_seed_is_reproducible(config: SandboxConfig) -> None:
    schema_sql, queries_sql, options = sample("blog")
    result = compile_sql(schema_sql, queries_sql, "", options)
    a = verify(result, options, config, seed=5, run_probes=False)
    b = verify(result, options, config, seed=5, run_probes=False)
    assert [(o.query_id, o.status, o.pg_rows) for o in a.queries] == [
        (o.query_id, o.status, o.pg_rows) for o in b.queries
    ]
    assert a.rows == b.rows


def test_user_provided_seed_data_is_used(config: SandboxConfig) -> None:
    schema = "CREATE TABLE p (id INT PRIMARY KEY, name TEXT NOT NULL); CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), n INT);"
    seed = "INSERT INTO p VALUES (1, 'a'), (2, 'b'); INSERT INTO c VALUES (1, 1, 10), (2, 1, 20), (3, 2, 30);"
    queries = "SELECT p.name, SUM(c.n) AS s FROM p JOIN c ON c.p_id = p.id GROUP BY p.name ORDER BY p.name;"
    result = compile_sql(schema, queries, seed)
    report = verify(result, None, config, seed_sql=seed)
    assert report.rows == {"p": 2, "c": 3} and report.queries[0].status == "MATCH"


def test_verify_rejects_invalid_input(config: SandboxConfig) -> None:
    with pytest.raises(VerificationError, match="schema errors"):
        verify(compile_sql("CREATE TABLE a (x INT REFERENCES ghost(id));"), None, config)
    many = "\n".join(f"SELECT * FROM t WHERE a = {i};" for i in range(31))
    with pytest.raises(VerificationError, match="at most 30"):
        verify(compile_sql("CREATE TABLE t (a INT);", many), None, config)
    with pytest.raises(VerificationError, match="SandboxConfig"):
        verify(compile_sql("CREATE TABLE t (a INT);"), None, None)
    with pytest.raises(VerificationError, match="circular"):
        verify(
            compile_sql(
                "CREATE TABLE a (id INT PRIMARY KEY, b INT NOT NULL); CREATE TABLE b (id INT PRIMARY KEY, a INT NOT NULL REFERENCES a(id));"
                "ALTER TABLE a ADD FOREIGN KEY (b) REFERENCES b(id);"
            ),
            None,
            config,
        )


def test_sql_is_re_emitted_from_the_ast_and_whitelisted() -> None:
    result = compile_sql("CREATE TABLE t (a INT PRIMARY KEY);", "SELECT   a  FROM t   WHERE a>1;")
    query = result.queries[0]
    assert safe_sql(query, result.schema_) == "SELECT a FROM t WHERE a > 1"
    hostile = parse("SELECT a FROM pg_catalog.pg_tables;", "queries").queries[0]
    with pytest.raises(UnsafeSqlError):
        safe_sql(hostile, result.schema_)  # type: ignore[arg-type]


def test_nothing_from_the_user_string_reaches_the_database_verbatim(config: SandboxConfig) -> None:
    """A comment-laden / oddly formatted query is re-emitted; the trailing payload never runs."""
    schema = "CREATE TABLE t (a INT PRIMARY KEY);"
    result = compile_sql(schema, "SELECT a FROM t; -- ; DROP SCHEMA public CASCADE\n", "")
    report = verify(result, None, config, run_probes=False)
    assert report.queries[0].status == "MATCH"
    with psycopg.connect(PG_DSN) as admin:
        assert admin.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'public'").fetchone()


# --------------------------------------------------------------------- corpus
def test_result_set_correctness_over_the_corpus(config: SandboxConfig) -> None:
    total = matches = 0
    unexplained: list[str] = []
    skipped = 0
    for case in load_corpus(DEFAULT_CORPUS):
        if not case.queries_sql.strip():
            continue
        result = compile_sql(case.schema_sql, case.queries_sql, "", CompileOptions())
        if result.has_errors:
            skipped += 1
            continue
        report = verify(result, CompileOptions(), config, seed=1, run_probes=False)
        for outcome in report.queries:
            if outcome.status in ("MATCH", "MISMATCH"):
                total += 1
                matches += outcome.status == "MATCH"
                if outcome.status == "MISMATCH" and not outcome.explained_by:
                    unexplained.append(
                        f"{case.name}/{outcome.query_id}: {outcome.sql} :: {outcome.hypothesis}"
                    )
    assert total >= 40, (total, skipped)
    assert matches / total >= 0.95, (matches, total)
    assert unexplained == []
