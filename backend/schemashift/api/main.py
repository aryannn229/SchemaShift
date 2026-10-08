"""FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from schemashift import __version__
from schemashift.api.middleware import BodyLimitMiddleware, RateLimiter, RequestMiddleware
from schemashift.api.routes import compile as compile_routes
from schemashift.api.routes import health
from schemashift.api.runs import AppState
from schemashift.api.settings import Settings, get_settings


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )


def init_sentry(dsn: str, environment: str) -> None:
    if not dsn:
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found,unused-ignore]
    except ImportError:  # optional dependency
        return
    sentry_sdk.init(dsn=dsn, environment=environment, send_default_pii=False)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings.sentry_dsn, settings.environment)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        state: AppState | None = getattr(app.state, "services", None)
        if state is not None:
            state.shutdown()

    app = FastAPI(
        title="SchemaShift",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    limiter = RateLimiter(
        {"compile": settings.compile_rate_per_minute, "verify": settings.verify_rate_per_minute}
    )
    # Added last = outermost: body limit first, then request logging / rate limit, then CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestMiddleware, limiter=limiter)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(compile_routes.router, prefix="/api/v1", tags=["compiler"])
    return app


app = create_app()
