"""Background run execution and persistence."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from schemashift.ai import LLMAdvisor
from schemashift.api.db.models import PlacementRow, QueryResultRow, Run, VerdictRow
from schemashift.api.db.session import DbCache, session_scope
from schemashift.api.schemas import CompileResponse, RunRequest
from schemashift.api.service import build_response, run_compile
from schemashift.api.settings import Settings
from schemashift.verification import SandboxConfig, verify

log = structlog.get_logger("schemashift.runs")


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
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")
        self.futures: dict[str, Future[None]] = {}

    @property
    def ai_enabled(self) -> bool:
        return bool(self.settings.anthropic_api_key)

    def submit(self, run_id: str, req: RunRequest) -> None:
        self.futures[run_id] = self.executor.submit(self._execute, run_id, req)

    def wait(self, run_id: str, timeout: float = 60.0) -> None:
        fut = self.futures.get(run_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def shutdown(self) -> None:
        """Graceful: let in-flight runs finish, then dispose the pool and engine."""
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.engine.dispose()

    # ------------------------------------------------------------------ execution
    def _execute(self, run_id: str, req: RunRequest) -> None:
        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(run_id=run_id)
        try:
            with session_scope(self.factory) as s:
                run = s.get(Run, run_id)
                assert run is not None
                run.status = "running"
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
        except Exception as exc:
            log.warning("run_failed", error=str(exc))
            with session_scope(self.factory) as s:
                run = s.get(Run, run_id)
                if run is not None:
                    run.status = "failed"
                    run.error = str(exc)[:2000]
                    run.duration_ms = int((time.perf_counter() - started) * 1000)
        finally:
            structlog.contextvars.unbind_contextvars("run_id")

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
