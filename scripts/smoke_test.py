"""Post-deploy smoke test: `python scripts/smoke_test.py https://your-api.example --origin https://your-app.example`.

Standard library only. Exits 1 when any check fails, so it can gate a deployment and run on a schedule.
It never sends anything but a tiny public sample schema; `--verify` also runs the sample against the
sandbox databases (PostgreSQL + MongoDB) of the target.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

SCHEMA = (
    "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);\n"
    "CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id), total NUMERIC(10,2));\n"
)
QUERIES = "SELECT u.email, o.total FROM users u JOIN orders o ON o.user_id = u.id WHERE o.total > 10;\n"


def call(
    base: str,
    method: str,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], Any]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    for k, v in {"Content-Type": "application/json", **(headers or {})}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - target chosen by the operator
            payload = res.read()
            status, hdrs = res.status, {k.lower(): v for k, v in res.headers.items()}
    except urllib.error.HTTPError as err:
        payload, status, hdrs = err.read(), err.code, {k.lower(): v for k, v in err.headers.items()}
    try:
        return status, hdrs, json.loads(payload) if payload else None
    except json.JSONDecodeError:
        return status, hdrs, payload.decode(errors="replace")


class Smoke:
    def __init__(self, base: str, origin: str | None, verify: bool, wait: float) -> None:
        self.base, self.origin, self.verify, self.wait = base, origin, verify, wait
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)

    def poll(self, run_id: str, timeout: float) -> dict[str, Any]:
        end = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < end:
            _, _, last = call(self.base, "GET", f"/api/v1/runs/{run_id}")
            if isinstance(last, dict) and last.get("status") in ("done", "failed", "cancelled"):
                return last
            time.sleep(1.0)
        return last if isinstance(last, dict) else {}

    def run(self) -> bool:
        b = self.base
        status, headers, body = call(b, "GET", "/api/v1/health")
        self.check("health returns 200", status == 200, str(body)[:80])
        self.check(
            "security headers",
            headers.get("x-content-type-options") == "nosniff"
            and headers.get("x-frame-options") == "DENY"
            and "default-src 'none'" in headers.get("content-security-policy", ""),
        )
        status, _, body = call(b, "POST", "/api/v1/compile", {"schema_sql": SCHEMA, "queries_sql": QUERIES})
        self.check(
            "compile returns a verdict",
            status == 200 and isinstance(body, dict) and body.get("overall_verdict") in ("SAFE", "CHANGED", "BROKEN"),
        )
        status, _, body = call(b, "GET", "/api/v1/samples")
        self.check("samples are served", status == 200 and isinstance(body, list) and len(body) > 0)
        status, _, body = call(b, "GET", "/api/v1/metrics")
        self.check("metrics endpoint answers", status in (200, 404))
        status, _, body = call(
            b, "POST", "/api/v1/runs", {"schema_sql": SCHEMA, "queries_sql": QUERIES, "verify": False}
        )
        run_id = body.get("run_id") if isinstance(body, dict) else None
        self.check("run accepted (202)", status == 202 and bool(run_id))
        if run_id:
            run = self.poll(run_id, self.wait)
            self.check("queued run completes", run.get("status") == "done", f"status={run.get('status')}")
        status, _, _ = call(b, "POST", "/api/v1/compile", raw=b'{"schema_sql": "' + b"x" * 400_000 + b'"}')
        self.check("oversized body is rejected (413)", status == 413, f"status={status}")
        if self.origin:
            _, ok_h, _ = call(
                b, "OPTIONS", "/api/v1/compile",
                headers={"Origin": self.origin, "Access-Control-Request-Method": "POST"},
            )
            self.check("CORS allows the app origin", ok_h.get("access-control-allow-origin") == self.origin)
        _, bad_h, _ = call(
            b, "OPTIONS", "/api/v1/compile",
            headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"},
        )
        self.check("CORS rejects other origins", "access-control-allow-origin" not in bad_h)
        if self.verify:
            status, _, body = call(
                b, "POST", "/api/v1/runs", {"schema_sql": SCHEMA, "queries_sql": QUERIES, "verify": True}
            )
            rid = body.get("run_id") if isinstance(body, dict) else None
            self.check("verified run accepted", status == 202 and bool(rid), f"status={status}")
            if rid:
                run = self.poll(rid, max(self.wait, 120.0))
                ver = run.get("verification") or {}
                queries = ver.get("queries") or []
                self.check(
                    "verified run matches PostgreSQL on every query",
                    run.get("status") == "done" and bool(queries) and all(q["status"] == "MATCH" for q in queries),
                    f"status={run.get('status')} error={str(run.get('error'))[:80]}",
                )
        return all(ok for _, ok, _ in self.results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base_url", help="API base URL, for example https://schemashift-api.onrender.com")
    ap.add_argument("--origin", help="the frontend origin that must be allowed by CORS")
    ap.add_argument("--verify", action="store_true", help="also run a verified run (needs the sandbox databases)")
    ap.add_argument("--wait", type=float, default=60.0, help="seconds to wait for a queued run")
    args = ap.parse_args()
    ok = Smoke(args.base_url, args.origin, args.verify, args.wait).run()
    print("\nsmoke test " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
