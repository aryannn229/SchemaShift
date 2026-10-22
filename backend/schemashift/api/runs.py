"""Background run execution, the job queue and persistence."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import psycopg
import structlog
from pymongo.errors import ConnectionFailure
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from schemashift.ai import LLMAdvisor
from schemashift.api.db.models import PlacementRow, QueryResultRow, Run, VerdictRow
from schemashift.api.db.session import DbCache, session_scope
from schemashift.api.schemas import CompileResponse, RunRequest
from schemashift.api.service import build_response, run_compile
from schemashift.api.settings import Settings
from schemashift.verification import SandboxConfig, verify

log = structlog.get_logger("schemashift.runs")

TRANSIENT = (psycopg.OperationalError, ConnectionFailure, OperationalError)
ACTIVE = ("pending", "running")


class TransientRunError(Exception):
    """An infrastructure error worth retrying (database connection lost, server unreachable)."""


class RunExecutor:
    """Executes one run and persists its status; used by the thread pool and by the queue worker."""

    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        advisor: LLMAdvisor,
        cache: DbCache,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.advisor = advisor
        self.cache = cache

    @property
    def ai_enabled(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    def mark(self, run_id: str, status: str, error: str | None = None) -> None:
        """Set a terminal status unless the run was already cancelled or finished."""
        with session_scope(self.factory) as s:
            run = s.get(Run, run_id)
            if run is not None and run.status in ACTIVE:
                run.status = status
                if error is not None:
                    run.error = error[:2000]

    def execute(self, run_id: str, req: RunRequest, attempt: int = 1) -> None:
        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(run_id=run_id, attempt=attempt)
        try:
            with session_scope(self.factory) as s:
                run = s.get(Run, run_id)
                if run is None or run.status not in ACTIVE:
                    return  # cancelled while queued
                run.status = "running"
                run.attempts = attempt
            result, codegen = run_compile(req, self.settings, self.advisor, self.cache)
            response = build_response(result, codegen, self.ai_enabled)
            verification: dict[str, Any] | None = None
            if req.verify and not result.has_errors:
                if not self.settings.verification_enabled:
                    raise RuntimeError("verification is not configured on this server")
                config = SandboxConfig(
                    pg_admin_dsn=self.settings.sandbox_admin_database_url,
                    mongo_url=self.settings.mongo_url,
                    mongo_sandbox_url=self.settings.mongo_sandbox_url or None,
                )
                report = verify(result, req.options, config, seed_sql=req.seed_sql, seed=req.seed)
                verification = report.model_dump(mode="json")
            self._store(run_id, response, verification, int((time.perf_counter() - started) * 1000))
            log.info("run_done", verdict=response.overall_verdict)
        except TRANSIENT as exc:
            if attempt < self.settings.job_max_tries:
                log.warning("run_retry", error=str(exc))
                with session_scope(self.factory) as s:
                    run = s.get(Run, run_id)
                    if run is not None and run.status == "running":
                        run.status = "pending"
                raise TransientRunError(str(exc)) from exc
            self._fail(run_id, exc, started)
        except Exception as exc:
            self._fail(run_id, exc, started)
        finally:
            structlog.contextvars.unbind_contextvars("run_id", "attempt")

    def _fail(self, run_id: str, exc: Exception, started: float) -> None:
        log.warning("run_failed", error=str(exc))
        with session_scope(self.factory) as s:
            run = s.get(Run, run_id)
            if run is not None and run.status in ACTIVE:
                run.status = "failed"
                run.error = str(exc)[:2000]
                run.duration_ms = int((time.perf_counter() - started) * 1000)

    def run_with_retries(self, run_id: str, req: RunRequest) -> None:
        """Thread-pool mode: retry transient errors in place with a short backoff."""
        for attempt in range(1, self.settings.job_max_tries + 1):
            try:
                self.execute(run_id, req, attempt)
                return
            except TransientRunError:
                time.sleep(min(2.0 * attempt, 10.0))
        self.mark(run_id, "failed", "retries exhausted")

    def _store(
        self,
        run_id: str,
        response: CompileResponse,
        verification: dict[str, Any] | None,
        duration_ms: int,
    ) -> None:
        with session_scope(self.factory) as s:
            run = s.get(Run, run_id)
            assert run is not None
            if run.status != "running":
                return  # cancelled (or timed out) while it was executing: keep that status
            run.status = "done"
            run.overall_verdict = response.overall_verdict
            run.counts = response.counts
            run.result = response.model_dump(mode="json")
            run.verification = verification
            run.duration_ms = duration_ms
            for v in response.verdicts:
                run.verdicts.append(
                    VerdictRow(
                        node_id=v.node_id,
                        rule_id=v.rule_id,
                        ir_node_type=v.ir_node_type,
                        status=v.status,
                        reason=v.reason,
                        mitigation=v.mitigation,
                        conditions=[c.model_dump(mode="json") for c in v.conditions_evaluated],
                        source_span=v.source_span.model_dump(mode="json")
                        if v.source_span
                        else None,
                    )
                )
            for d in response.plan:
                sug = d.ai.suggestion if d.ai else None
                run.placements.append(
                    PlacementRow(
                        relationship_id=d.relationship_id,
                        decision=d.decision,
                        score=d.score,
                        top_factors=[f.model_dump(mode="json") for f in d.top_factors],
                        ai_decision=sug.decision if sug else None,
                        ai_confidence=sug.confidence if sug else None,
                        ai_justification=sug.justification if sug else None,
                        agree=d.agree,
                    )
                )
            for q in (verification or {}).get("queries", []):
                run.query_results.append(
                    QueryResultRow(
                        query_id=q["query_id"],
                        sql=q["sql"],
                        pipeline=None,
                        status=q["status"],
                        pg_rows=q.get("pg_rows", 0),
                        mongo_rows=q.get("mongo_rows", 0),
                        diff=q.get("differing") or None,
                    )
                )


class QueueClient:
    """Enqueue and abort jobs on the arq (Redis) queue from synchronous code.

    arq is asyncio-only, so a private event loop thread owns the Redis pool.
    """

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="arq-client", daemon=True
        )
        self._thread.start()
        self._pool: Any = None

    def _call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=10)

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def _enqueue(self, run_id: str, payload: dict[str, Any]) -> bool:
        pool = await self._get_pool()
        job = await pool.enqueue_job("execute_run", run_id, payload, _job_id=run_id)
        return job is not None

    async def _abort(self, run_id: str) -> bool:
        from arq.jobs import Job

        pool = await self._get_pool()
        return bool(await Job(run_id, pool).abort(timeout=2, poll_delay=0.2))

    def enqueue(self, run_id: str, req: RunRequest) -> bool:
        return bool(self._call(self._enqueue(run_id, req.model_dump(mode="json"))))

    def abort(self, run_id: str) -> bool:
        try:
            return bool(self._call(self._abort(run_id)))
        except Exception:  # noqa: BLE001 - the run is already marked cancelled in the database
            return False

    def close(self) -> None:
        if self._pool is not None:
            try:
                self._call(self._pool.aclose())
            except Exception:  # noqa: BLE001, S110
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)


class AppState:
    """Process-wide services, created at startup and closed at shutdown."""

    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        factory: sessionmaker[Session],
        advisor: LLMAdvisor,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.factory = factory
        self.advisor = advisor
        self.cache = DbCache(factory)
        self.runner = RunExecutor(settings, factory, advisor, self.cache)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")
        self.futures: dict[str, Future[None]] = {}
        self.queue: QueueClient | None = (
            QueueClient(settings.redis_url) if settings.redis_url else None
        )

    @property
    def ai_enabled(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    def submit(self, run_id: str, req: RunRequest) -> None:
        """Queue the run on Redis when configured, otherwise on the in-process thread pool."""
        if self.queue is not None:
            try:
                if self.queue.enqueue(run_id, req):
                    return
            except Exception as exc:  # noqa: BLE001 - Redis down: degrade to local execution
                log.warning("queue_unavailable", error=str(exc))
        self.futures[run_id] = self.executor.submit(self.runner.run_with_retries, run_id, req)

    def cancel(self, run_id: str) -> str:
        """Cancel a pending or running run; returns its status after the call."""
        with session_scope(self.factory) as s:
            run = s.get(Run, run_id)
            if run is None:
                return "missing"
            if run.status not in ACTIVE:
                return run.status
            run.status = "cancelled"
            run.error = "cancelled by the user"
        fut = self.futures.get(run_id)
        if fut is not None:
            fut.cancel()
        if self.queue is not None:
            self.queue.abort(run_id)
        return "cancelled"

    def wait(self, run_id: str, timeout: float = 60.0) -> None:
        fut = self.futures.get(run_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def shutdown(self) -> None:
        """Graceful: let in-flight runs finish, then dispose the pool and engine."""
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.queue is not None:
            self.queue.close()
        self.engine.dispose()
