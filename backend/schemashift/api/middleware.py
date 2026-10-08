"""Request-size limit, rate limiting, security headers and per-request logging."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import MutableMapping
from typing import Any

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger("schemashift.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class BodyLimitMiddleware:
    """Reject bodies over ``max_bytes`` (by Content-Length, and by counting streamed chunks)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return
        seen = 0
        too_big = False

        async def limited() -> Message:
            nonlocal seen, too_big
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    too_big = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if too_big and not started:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited, guarded_send)
        if too_big and not started:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": f"request body exceeds {self.max_bytes} bytes"}, status_code=413
        )
        await response(scope, receive, send)


class RateLimiter:
    """Sliding-window limiter keyed by (client ip, bucket)."""

    def __init__(self, limits: dict[str, int], window: float = 60.0) -> None:
        self.limits = limits
        self.window = window
        self._hits: MutableMapping[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, client: str, bucket: str, now: float | None = None) -> bool:
        limit = self.limits.get(bucket)
        if limit is None:
            return True
        now = time.monotonic() if now is None else now
        hits = self._hits[(client, bucket)]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def bucket_for(request: Request) -> str | None:
    if request.method != "POST":
        return None
    path = request.url.path
    if path.endswith("/compile") or path.endswith("/runs"):
        return "compile"
    return None


class RequestMiddleware:
    """Adds request ids, security headers, structured logs and POST rate limits."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        request_id = uuid.uuid4().hex[:12]
        client = request.client.host if request.client else "unknown"
        bucket = bucket_for(request)
        started = time.perf_counter()
        status = 500

        async def wrapped_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers: list[Any] = list(message.get("headers", []))
                for k, v in SECURITY_HEADERS.items():
                    headers.append((k.lower().encode(), v.encode()))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            if bucket and not self.limiter.allow(client, bucket):
                response: Response = JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, wrapped_send)
            else:
                await self.app(scope, receive, wrapped_send)
        finally:
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=status,
                ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()
