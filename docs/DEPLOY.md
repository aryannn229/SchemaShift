# Deploying SchemaShift

Targets: **Vercel** (frontend), **Render** (API + worker + Redis), **Neon** (PostgreSQL), **MongoDB Atlas** (replica set).
This repository contains every config and a smoke test; the deployment itself is run by the owner (see "What is verified").

```
Browser ─> Vercel (static SPA) ─> Render web service (FastAPI, 2 workers) ──┬─> Neon PostgreSQL (app DB + sandbox DB)
                                       │                                    └─> Atlas MongoDB (a database per verified run)
                                       └─ Redis (Render Key Value) <─ Render worker (arq: runs, retries, timeouts)
```

## 1. Environment variables
| Variable | Service | Required | Meaning |
|---|---|---|---|
| `APP_DATABASE_URL` | api, worker | yes | `postgresql+psycopg://user:pass@host/db?sslmode=require` (Neon, database `schemashift_app`). Empty = local SQLite (development only). |
| `SANDBOX_ADMIN_DATABASE_URL` | api, worker | for verification | `postgresql://...` role that may `CREATE ROLE`/`CREATE SCHEMA` in a **separate** database (`schemashift_sandbox`). |
| `MONGO_URL` | api, worker | for verification | Atlas `mongodb+srv://...` (replica set; transactions need it). |
| `MONGO_SANDBOX_URL` | api, worker | recommended | Atlas user limited to `readWrite` on `run_*`. Falls back to `MONGO_URL`. |
| `REDIS_URL` | api, worker | yes in production | Shared rate limits and the job queue. Empty = in-process limits and a thread pool (local development). |
| `CORS_ORIGINS` | api | yes | Comma separated allow list, the Vercel URL(s). |
| `JOB_TIMEOUT_S` / `JOB_MAX_TRIES` | api, worker | no | Job timeout (300) and attempts for transient errors (3). |
| `ANTHROPIC_API_KEY`, `LLM_MODEL` | api, worker | no | Advisory AI opinion; without a key a deterministic mock is used and the UI says so. |
| `SENTRY_DSN`, `LOG_LEVEL` | api, worker | no | Error reporting; JSON logs with `request_id` / `run_id` / `attempt`. |
| `VITE_API_URL` | frontend build | yes | Public API URL. |
Never commit `.env` or secrets; they live in each host's settings.

## 2. Databases
**Neon.** One project, two databases: `schemashift_app` and `schemashift_sandbox`. In the sandbox database:
```sql
CREATE ROLE sandbox_admin LOGIN PASSWORD '<strong password>' CREATEROLE;
GRANT CREATE ON DATABASE schemashift_sandbox TO sandbox_admin;
```
**Atlas.** Free M0 clusters are replica sets. Create user `app` (`readWrite` on anything) for `MONGO_URL` and, if you want least privilege,
`sandbox` limited to `run_*` databases for `MONGO_SANDBOX_URL`. Allow the Render outbound IPs in Network Access.

## 3. Render (API + worker + Redis)
1. New > Blueprint > this repository. `render.yaml` creates `schemashift-api` (Docker, health check `/api/v1/health`, runs `alembic upgrade head` then 2 uvicorn workers), `schemashift-worker` (`arq schemashift.worker.WorkerSettings`) and the Key Value store `schemashift-redis` (wired into both as `REDIS_URL`).
2. Fill the `sync: false` variables for **both** services (same values for the database and Mongo URLs).
3. `CORS_ORIGINS` = the Vercel URL, set after step 4 and redeploy.

## 4. Vercel (frontend)
Import the repo, root directory `frontend`, framework Vite, build `npm run build`, output `dist`, env `VITE_API_URL=<Render URL>`. `vercel.json` rewrites all paths to `index.html`.

## 5. Smoke test and status badge
```
python scripts/smoke_test.py https://<api>.onrender.com --origin https://<app>.vercel.app --verify
```
It checks health, security headers, a compile, a queued run, the 413 body limit, the CORS allow list (allowed origin accepted, others not)
and, with `--verify`, a verified run that must match PostgreSQL on every query. Exit code 1 on any failure.
Set the repository variables `API_URL` and `APP_ORIGIN`; `.github/workflows/smoke.yml` then runs it after each deployment and every 6 hours.
The README badge shows the result of that workflow (uptime/status). **A deployment counts as done only when this smoke test passes against the live URL.**

## 6. Operating notes
- Rate limits (30 compile/min, 5 verified/min per IP) are shared across workers through Redis; if Redis is unreachable the API falls back to per-process limits and logs `rate_limit_redis_unavailable`.
- Runs: status `pending` (queued) > `running` > `done` / `failed` / `cancelled`, persisted in PostgreSQL with the attempt count. Transient database/connection errors are retried (default 3 attempts), a job running longer than `JOB_TIMEOUT_S` is marked `failed`, `POST /api/v1/runs/{id}/cancel` stops a queued or running run.
- Sandbox leftovers (`run_*` roles/schemas/databases) older than an hour are swept by the next run; see `docs/VERIFICATION.md`.

## 7. What is verified here
Locally, against `docker-compose.prod.yml` (same image and commands as Render, plus Redis, PostgreSQL 16 and a MongoDB 7 replica set):
`docker compose -f docker-compose.prod.yml up -d --build --wait` then the smoke test above. CI repeats this on every push (job `prod-stack`).
Not done from this repository: creating Neon/Atlas/Render/Vercel resources or running the smoke test against them. Those steps are marked
**ready for owner to run** in `docs/progress/STATUS.md`.

## 8. Security checklist (re-checked in Phase 21, tests in `tests/unit/api/test_phase21.py`)
Raw user SQL is never executed (compile only parses; verification runs AST re-emissions through a whitelist with bound parameters);
request body limit (413); CORS allow list; security headers on every response; the LLM prompt contains table structure and aggregate
statistics only, never seed rows or query literals.
