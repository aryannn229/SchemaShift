"""Concurrency probes and the guarded helpers, against a real replica set."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import OperationFailure

from schemashift.models.concurrency import ConcurrencyResult
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification import SandboxConfig, verify
from schemashift.verification.concurrency import _Probe
from schemashift.verification.sandbox import Sandbox
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration

SCHEMA = """
CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);
CREATE TABLE orders (id INT PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                     ref TEXT NOT NULL UNIQUE);
CREATE TABLE notes (id INT PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id), tag TEXT NOT NULL UNIQUE);
"""
QUERIES = (
    "BEGIN; UPDATE users SET email = 'x@y.z' WHERE id = 1; DELETE FROM orders WHERE id = 1; COMMIT;"
)
OPTIONS = CompileOptions(
    preserve_integer_ids=True,
    relationship_overrides={
        "fk:orders.user_id->users.id": "REFERENCE",
        "fk:notes.user_id->users.id": "EMBED",
    },
)
FK = "conc:fk_race:fk:orders.user_id->users.id"


@pytest.fixture
def config(pg_dsn: str, mongo_client: Any) -> SandboxConfig:
    return SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL)


@pytest.fixture
def report(config: SandboxConfig) -> Any:
    result = compile_sql(SCHEMA, QUERIES, "", OPTIONS)
    rep = verify(
        result,
        OPTIONS,
        config,
        seed=3,
        counterexamples=False,
        run_probes=False,
        concurrency_iterations=40,
    )
    return result, rep


def by(results: list[ConcurrencyResult], scenario: str, strategy: str) -> ConcurrencyResult:
    return next(r for r in results if r.scenario_id == scenario and r.strategy == strategy)


def test_the_generated_helpers_let_nothing_through(report: Any) -> None:
    _, rep = report
    generated = [r for r in rep.concurrency if r.strategy == "generated"]
    assert {r.kind for r in generated} == {"fk_race", "duplicate_insert", "counter", "transaction"}
    assert all(r.violations == 0 for r in generated), [
        (r.scenario_id, r.violations) for r in generated if r.violations
    ]
    assert all(r.iterations >= 40 or r.kind in ("counter", "transaction") for r in generated)


def test_the_unguarded_variants_show_the_hazard_is_real(report: Any) -> None:
    _, rep = report
    # the same operations without the guard: orphans, duplicates, repeated ids, lost updates
    assert by(rep.concurrency, FK, "unguarded").violations > 0
    assert by(rep.concurrency, "conc:dup:notes.tag", "unguarded").violations > 0
    assert by(rep.concurrency, "conc:counter:users.id", "unguarded").violations > 0
    assert by(rep.concurrency, "conc:txn:t1", "unguarded").violations > 0


def test_results_name_their_verdicts_and_are_reproducible_in_their_inputs(report: Any) -> None:
    result, rep = report
    ids = {v.node_id for v in result.concurrency.verdicts}
    assert {r.scenario_id for r in rep.concurrency} <= ids
    assert all(r.seed == 3 and r.threads >= 2 and r.duration_ms >= 0 for r in rep.concurrency)
    txn = by(rep.concurrency, "conc:txn:t1", "generated")
    assert "retries after write conflicts" in txn.detail  # the callback really did run again


def test_a_lock_document_makes_two_transactions_conflict(config: SandboxConfig) -> None:
    """The mechanism the FK guard relies on: writing the same lock document conflicts."""
    result = compile_sql(SCHEMA, "", "", OPTIONS)
    from schemashift.codegen import generate_code
    from schemashift.verification.runner import run_python_source

    code = generate_code(result, OPTIONS)
    ns = run_python_source(code.file("python/helpers.py").content, "")
    with Sandbox(config) as sb:
        db = sb.mongo_db
        with db.client.start_session() as s1, db.client.start_session() as s2:
            s1.start_transaction()
            s2.start_transaction()
            # a transaction's snapshot is taken at its first operation: take both before either writes
            db["_locks"].find_one({}, session=s1)
            db["_locks"].find_one({}, session=s2)
            ns["lock_row"](db, "users", {"id": 1}, s1)
            s1.commit_transaction()
            # the second transaction started before the first committed: whether the conflict is
            # reported by the write or by the commit, it is a retryable TransientTransactionError
            with pytest.raises(OperationFailure) as err:
                ns["lock_row"](db, "users", {"id": 1}, s2)
                s2.commit_transaction()
            assert err.value.has_error_label(
                "TransientTransactionError"
            )  # with_transaction retries it
            if s2.in_transaction:
                s2.abort_transaction()
        ns["GUARDS"] = False  # switched off: no lock, no conflict
        with db.client.start_session() as s3:
            s3.start_transaction()
            ns["lock_row"](db, "users", {"id": 2}, s3)
            s3.abort_transaction()
        assert db["_locks"].count_documents({"_id": "users|id=2"}) == 0


def test_sequential_duplicates_and_missing_hosts_still_raise_clear_errors(
    config: SandboxConfig,
) -> None:
    result = compile_sql(SCHEMA, "", "", OPTIONS)
    from schemashift.codegen import generate_code
    from schemashift.verification.runner import run_python_source

    ns = run_python_source(generate_code(result, OPTIONS).file("python/helpers.py").content, "")
    plan, integrity = ns["PLAN"], ns["IntegrityError"]
    with Sandbox(config) as sb:
        db = sb.mongo_db
        ns["insert_row"](db, plan, "users", {"id": 1, "email": "a@x"}, None)
        ns["insert_row"](db, plan, "notes", {"id": 1, "user_id": 1, "tag": "t"}, None)
        with pytest.raises(integrity, match="duplicate key"):
            ns["insert_row"](db, plan, "notes", {"id": 2, "user_id": 1, "tag": "t"}, None)
        with pytest.raises(integrity, match="no users row"):
            ns["insert_row"](db, plan, "notes", {"id": 3, "user_id": 999, "tag": "u"}, None)
        assert len(ns["locate"](db, plan, "notes", {"user_id": 1})) == 1


def test_the_probe_reports_skips_instead_of_failing(config: SandboxConfig) -> None:
    """A uuid key cannot be varied: the scenario is reported as skipped, with the reason."""
    schema = (
        "CREATE TABLE a (id UUID PRIMARY KEY);"
        "CREATE TABLE b (id UUID PRIMARY KEY, a_id UUID NOT NULL REFERENCES a(id));"
    )
    opts = CompileOptions(relationship_overrides={"fk:b.a_id->a.id": "REFERENCE"})
    result = compile_sql(schema, "", "", opts)
    rep = verify(
        result,
        opts,
        config,
        seed=1,
        counterexamples=False,
        run_probes=False,
        concurrency_iterations=5,
    )
    assert any("integer or text" in s.reason for s in rep.concurrency_skips)
    assert not [r for r in rep.concurrency if r.violations]


def test_probe_class_exposes_a_seeded_race_helper(config: SandboxConfig) -> None:
    import random

    result = compile_sql("CREATE TABLE t (id INT PRIMARY KEY);", "", "", CompileOptions())
    with Sandbox(config) as sb:
        from schemashift.codegen import generate_code
        from schemashift.codegen.layout import build_layouts
        from schemashift.verification.ddl import ddl_statements
        from schemashift.verification.runner import load_env
        from schemashift.verification.seed import generate_seed

        codegen = generate_code(result, CompileOptions())
        env, _, _ = load_env(
            result,
            CompileOptions(),
            sb,
            codegen,
            build_layouts(result.graph, result.plan),
            ddl_statements(result.schema_)[0],
            generate_seed(result.schema_, 1, 2),
        )
        p = _Probe(env, seed=7, threads=2, iterations=1)
        out = p.race(
            [lambda: 1, lambda: (_ for _ in ()).throw(ValueError("boom"))], random.Random(7)
        )
        assert out[0] is None and isinstance(out[1], ValueError)
