"""arq worker: ``arq schemashift.worker.WorkerSettings``.

Jobs carry only the run id and the request; status, attempts and results live in PostgreSQL. A job
is retried (``Retry``) on transient infrastructure errors up to ``JOB_MAX_TRIES`` times, stopped
after ``JOB_TIMEOUT_S`` seconds (status ``failed``) and can be cancelled with
``POST /runs/{id}/cancel``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from arq.connections import RedisSettings
from arq.worker import Retry

from schemashift.ai import make_advisor
from schemashift.api.db.session import DbCache, create_all, make_engine, make_session_factory
from schemashift.api.runs import RunExecutor, TransientRunError
from schemashift.api.schemas import RunRequest
from schemashift.api.settings import get_settings

log = structlog.get_logger("schemashift.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    if settings.db_auto_create:
        create_all(engine)
    factory = make_session_factory(engine)
    advisor = make_advisor(settings.anthropic_api_key, settings.llm_model)
    ctx["engine"] = engine
    ctx["executor"] = RunExecutor(settings, factory, advisor, DbCache(factory))


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx["engine"].dispose()


async def execute_run(ctx: dict[str, Any], run_id: str, payload: dict[str, Any]) -> None:
    executor: RunExecutor = ctx["executor"]
    req = RunRequest.model_validate(payload)
    attempt = int(ctx.get("job_try", 1))
    try:
        await asyncio.to_thread(executor.execute, run_id, req, attempt)
    except TransientRunError as exc:
        raise Retry(defer=min(5 * attempt, 30)) from exc
    except asyncio.CancelledError:
        # a job timeout or abort cancelled this coroutine (a user cancel set its status already)
        executor.mark(run_id, "failed", f"stopped after {executor.settings.job_timeout_s}s")
        raise


_settings = get_settings()


class WorkerSettings:
    functions = [execute_run]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url or "redis://localhost:6379")
    job_timeout = _settings.job_timeout_s
    max_tries = _settings.job_max_tries
    allow_abort_jobs = True
    keep_result = 60
    max_jobs = 2
