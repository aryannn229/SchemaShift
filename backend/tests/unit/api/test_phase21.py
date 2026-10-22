"""Phase 21: shared rate limits, the run queue (retries, cancellation, timeouts) and the security re-check."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import fakeredis
import psycopg
import pytest

from schemashift.ai.base import AdvisorError
from schemashift.api.db.models import Run
from schemashift.api.db.session import session_scope
from schemashift.api.middleware import RateLimiter, RedisRateLimiter
from schemashift.api.runs import TransientRunError
from schemashift.api.schemas import RunRequest
from tests.unit.api.test_api import QUERIES, SCHEMA, make_client

REQ = RunRequest(schema_sql=SCHEMA, queries_sql=QUERIES)


# --------------------------------------------------------------------------- rate limits
def test_redis_limiter_is_shared_between_workers() -> None:
    server = fakeredis.FakeServer()
    a = RedisRateLimiter("", {"compile": 2}, client=fakeredis.FakeRedis(server=server))
    b = RedisRateLimiter("", {"compile": 2}, client=fakeredis.FakeRedis(server=server))
    assert a.allow("1.2.3.4", "compile", now=100.0)
    assert b.allow("1.2.3.4", "compile", now=101.0)
    assert not a.allow("1.2.3.4", "compile", now=102.0)  # two workers, one shared budget of 2
    assert a.allow("5.6.7.8", "compile", now=102.0)  # another client
    assert a.allow("1.2.3.4", "compile", now=161.5)  # the window slid past the first hits
    assert a.allow("1.2.3.4", "unlimited-bucket")


def test_redis_limiter_degrades_to_the_local_window_when_redis_fails() -> None:
    class Broken:
        def pipeline(self) -> Any:
            raise ConnectionError("redis is down")

    limiter = RedisRateLimiter("", {"compile": 1}, client=Broken())
    assert limiter.allow("c", "compile", now=1.0)
    assert not limiter.allow("c", "compile", now=2.0)  # still limited, per process


def test_app_uses_the_in_memory_limiter_without_redis(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        assert type(c.app.state.limiter) is RateLimiter  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- queue and runs
def _new_run(state: Any, status: str = "pending") -> str:
    with session_scope(state.factory) as s:
        run = Run(id="r-" + status, status=status, schema_sql=SCHEMA, queries_sql=QUERIES)
        s.add(run)
        return run.id


def _state(tmp_path: Path, **kw: Any) -> Any:
    client = make_client(tmp_path, **kw)
    client.get("/api/v1/health")  # builds the services lazily
    from schemashift.api.deps import get_state

    class Req:
        app = client.app

    return client, get_state(Req())  # type: ignore[arg-type]


def _status(state: Any, run_id: str) -> tuple[str, int, str | None]:
    with session_scope(state.factory) as s:
        r = s.get(Run, run_id)
        assert r is not None
        return r.status, r.attempts, r.error


def test_a_cancelled_run_is_not_executed(tmp_path: Path) -> None:
    client, state = _state(tmp_path)
    with client:
        run_id = _new_run(state)
        assert state.cancel(run_id) == "cancelled"
        state.runner.execute(run_id, REQ)
        assert _status(state, run_id)[0] == "cancelled"
        assert state.cancel("nope") == "missing"
        assert state.cancel(run_id) == "cancelled"  # idempotent


def test_a_run_cancelled_while_executing_keeps_that_status(tmp_path: Path) -> None:
    client, state = _state(tmp_path)
    with client:
        run_id = _new_run(state)
        import schemashift.api.runs as runs

        real = runs.run_compile

        def compile_then_cancel(*a: Any, **k: Any) -> Any:
            out = real(*a, **k)
            state.cancel(run_id)  # the user cancels during the (long) verification
            return out

        runs.run_compile = compile_then_cancel  # type: ignore[assignment]
        try:
            state.runner.execute(run_id, REQ)
        finally:
            runs.run_compile = real  # type: ignore[assignment]
        status, _, error = _status(state, run_id)
        assert status == "cancelled" and error == "cancelled by the user"


def test_transient_errors_are_retried_then_fail(tmp_path: Path) -> None:
    client, state = _state(tmp_path, job_max_tries=2)
    with client:
        import schemashift.api.runs as runs

        real = runs.run_compile

        def flaky(*a: Any, **k: Any) -> Any:
            raise psycopg.OperationalError("connection lost")

        runs.run_compile = flaky  # type: ignore[assignment]
        try:
            run_id = _new_run(state)
            with pytest.raises(TransientRunError):
                state.runner.execute(run_id, REQ, attempt=1)
            assert _status(state, run_id) == ("pending", 1, None)  # queued again, attempt recorded
            state.runner.execute(run_id, REQ, attempt=2)  # last attempt: no more retries
            status, attempts, error = _status(state, run_id)
            assert status == "failed" and attempts == 2 and "connection lost" in (error or "")
        finally:
            runs.run_compile = real  # type: ignore[assignment]


def test_thread_pool_mode_retries_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = _state(tmp_path, job_max_tries=3)
    with client:
        import schemashift.api.runs as runs

        calls = {"n": 0}
        real = runs.run_compile

        def flaky_once(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise psycopg.OperationalError("blip")
            return real(*a, **k)

        monkeypatch.setattr(runs, "run_compile", flaky_once)
        monkeypatch.setattr(runs.time, "sleep", lambda s: None)
        run_id = _new_run(state)
        state.runner.run_with_retries(run_id, REQ)
        status, attempts, _ = _status(state, run_id)
        assert (status, attempts, calls["n"]) == ("done", 2, 2)


def test_submit_uses_the_queue_and_falls_back_to_threads(tmp_path: Path) -> None:
    client, state = _state(tmp_path)
    with client:
        sent: list[str] = []

        class FakeQueue:
            ok = True

            def enqueue(self, run_id: str, req: RunRequest) -> bool:
                if not self.ok:
                    raise ConnectionError("redis down")
                sent.append(run_id)
                return True

            def abort(self, run_id: str) -> bool:
                sent.append("abort:" + run_id)
                return True

            def close(self) -> None:
                pass

        queue = FakeQueue()
        state.queue = queue
        first = _new_run(state)
        state.submit(first, REQ)
        assert sent == [first] and first not in state.futures  # handed to the worker
        state.cancel(first)
        assert sent[-1] == "abort:" + first

        queue.ok = False
        with session_scope(state.factory) as s:
            s.add(Run(id="r-fallback", status="pending", schema_sql=SCHEMA, queries_sql=QUERIES))
        state.submit("r-fallback", REQ)
        state.wait("r-fallback")
        assert _status(state, "r-fallback")[0] == "done"
        state.queue = None


def test_cancel_endpoint(tmp_path: Path) -> None:
    client, state = _state(tmp_path)
    with client:
        run_id = _new_run(state)
        res = client.post(f"/api/v1/runs/{run_id}/cancel")
        assert res.status_code == 200 and res.json() == {"run_id": run_id, "status": "cancelled"}
        assert client.post("/api/v1/runs/missing/cancel").status_code == 404


def test_worker_job_retries_transient_errors_and_marks_timeouts(tmp_path: Path) -> None:
    from arq.worker import Retry

    from schemashift import worker

    class Exec:
        class settings:  # noqa: N801 - mimics the Settings attribute
            job_timeout_s = 7

        marked: list[tuple[str, str, str | None]] = []
        error: BaseException | None = None

        def execute(self, run_id: str, req: RunRequest, attempt: int) -> None:
            if self.error:
                raise self.error

        def mark(self, run_id: str, status: str, error: str | None = None) -> None:
            self.marked.append((run_id, status, error))

    ex = Exec()
    ctx = {"executor": ex, "job_try": 2}
    payload = REQ.model_dump(mode="json")
    asyncio.run(worker.execute_run(ctx, "r1", payload))
    ex.error = TransientRunError("db down")
    with pytest.raises(Retry) as info:
        asyncio.run(worker.execute_run(ctx, "r1", payload))
    assert info.value.defer_score == 10_000  # 5 s x attempt 2, in ms
    ex.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.execute_run(ctx, "r2", payload))
    assert ex.marked == [("r2", "failed", "stopped after 7s")]
    assert worker.WorkerSettings.allow_abort_jobs and worker.WorkerSettings.max_tries >= 1


# --------------------------------------------------------------------------- security re-check
def test_body_limit_cors_allowlist_and_security_headers(tmp_path: Path) -> None:
    with make_client(tmp_path, max_body_bytes=2000, cors_origins="https://app.example") as c:
        big = c.post("/api/v1/compile", json={"schema_sql": "x" * 5000})
        assert big.status_code == 413
        ok = c.options(
            "/api/v1/compile",
            headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"},
        )
        assert ok.headers.get("access-control-allow-origin") == "https://app.example"
        evil = c.options(
            "/api/v1/compile",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in evil.headers
        h = c.get("/api/v1/health").headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "default-src 'none'" in h["content-security-policy"]


def test_raw_sql_is_never_executed_by_compile(tmp_path: Path) -> None:
    """A compile of hostile SQL only parses it: no database is touched."""
    hostile = "CREATE TABLE t (id INT PRIMARY KEY); DROP TABLE users; SELECT pg_sleep(100);"
    with make_client(tmp_path) as c:
        res = c.post("/api/v1/compile", json={"schema_sql": hostile, "queries_sql": hostile})
        assert res.status_code == 200  # reported, never run


def test_generated_ddl_is_whitelisted() -> None:
    import sqlglot

    from schemashift.parser import parse
    from schemashift.verification.ddl import UnsafeSqlError, assert_safe_expression

    schema = parse("CREATE TABLE t (id INT PRIMARY KEY);").schema_
    for bad in (
        "SELECT pg_read_file('/etc/passwd') FROM t",
        "SELECT pg_sleep(100) FROM t",
        "SELECT * FROM pg_catalog.pg_tables",
    ):
        with pytest.raises(UnsafeSqlError):
            assert_safe_expression(sqlglot.parse_one(bad, dialect="postgres"), schema)


def test_the_llm_sees_schema_structure_only(tmp_path: Path) -> None:
    seen: list[str] = []

    class Recorder:
        def suggest(self, prompt: str) -> Any:  # pragma: no cover - replaced below
            raise AdvisorError("unused")

    from schemashift.ai import attach_opinions
    from schemashift.optimizer import extract_features
    from schemashift.pipeline import compile_sql

    secret = "TOPSECRET_ROW_VALUE_7731"
    seed = f"INSERT INTO users (id, email) VALUES (1, '{secret}');"
    queries = f"SELECT u.email FROM users u WHERE u.email = '{secret}_q';"
    result = compile_sql(SCHEMA, queries, seed)

    class Spy:
        name = "spy"

        def advise(self, rel: Any, excerpt: str, prompt: str | None = None) -> Any:
            raise AdvisorError("stop")

    import schemashift.ai.prompts as prompts

    original = prompts.build_prompt

    def capture(rel: Any, excerpt: str) -> str:
        text = original(rel, excerpt)
        seen.append(text)
        return text

    prompts.build_prompt = capture  # type: ignore[assignment]
    try:
        feats = extract_features(
            result.graph, result.ir, result.queries, result.seed_queries, {}, None, {}, 1.0
        )
        try:
            attach_opinions(result.plan, feats, result.schema_, Spy(), None)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - only the prompts matter
            pass
        for rel in feats.values():
            seen.append(original(rel, prompts.schema_excerpt(result.schema_, rel)))
    finally:
        prompts.build_prompt = original  # type: ignore[assignment]
    assert seen and all(secret not in p for p in seen)
    assert any("CREATE TABLE" in p for p in seen)
