# Phase 21: production infrastructure (deployment is for the owner to run)

## What changed
- **Shared rate limits**: `RedisRateLimiter` (sliding window in a sorted set) shared by every worker; falls back to the in-process window when Redis is unreachable
  (logged `rate_limit_redis_unavailable`). Without `REDIS_URL` everything works as before.
- **Job queue**: arq (decision in DECISIONS.md). `POST /runs` enqueues; `schemashift.worker.WorkerSettings` executes with retries (transient database/connection errors,
  `JOB_MAX_TRIES`, default 3), a timeout (`JOB_TIMEOUT_S`, 300) and `POST /runs/{id}/cancel`. Run status and attempts are persisted in PostgreSQL (Alembic 0002).
  Without Redis the thread pool runs the same `RunExecutor` with in-place retries.
- **Deployment files**: `render.yaml` (API, worker, Key Value), `docker-compose.prod.yml` (same image and commands plus Redis, PostgreSQL 16, MongoDB replica set),
  `docs/DEPLOY.md` (exact steps and every variable), `scripts/smoke_test.py`, `.github/workflows/smoke.yml` (post-deploy and every 6 hours), README badge, CI job `prod-stack`.
- **Security re-check** (`tests/unit/api/test_phase21.py`): raw SQL is never executed by `/compile`; the DDL whitelist rejects `pg_read_file`, `pg_sleep`, catalog access;
  body limit 413; CORS allow list; security headers; the LLM prompt contains table structure only (a sentinel in seed rows and queries never reaches it).

## Verified locally against the production-like stack (2026-10-23)
`API_PORT=8010 docker compose -f docker-compose.prod.yml up -d --build --wait`, then `python scripts/smoke_test.py http://localhost:8010 --origin http://localhost:5173 --verify`:
12 of 12 checks passed (health, security headers, compile, samples, metrics, queued run completes through the **worker container**, 413, CORS allowed and rejected, verified run
matches PostgreSQL on every query). A verified run was cancelled and stayed `cancelled`; 36 compile requests gave 26 x 200 and 10 x 429 across the two uvicorn workers
(Redis keys `schemashift:rl:*`). `alembic upgrade head` applied 0001 and 0002 in the container.
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## NOT done (ready for owner to run)
Creating the Neon, Atlas, Render and Vercel resources and running the smoke test against the live URL. **Deployment is not done until that smoke test passes against the
live URL**; the exact steps are in `docs/DEPLOY.md`. Nothing live was touched from here.

## Known limitations
- The dev SQLite file is not migrated automatically: delete `schemashift_dev.db` after upgrading (production uses Alembic).
- `uptime` is the smoke workflow badge; it shows nothing until `API_URL` and `APP_ORIGIN` repository variables exist.
