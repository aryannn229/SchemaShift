# Deployment

Target: free or cheap tiers. Frontend on Vercel, backend on Render (Railway and Fly.io work the same
way: they run `backend/Dockerfile`), PostgreSQL on Neon, MongoDB on Atlas.

```
Browser ──> Vercel (static SPA) ──> Render (FastAPI, 2 workers)
                                      ├── Neon PostgreSQL  (app database + verification sandbox database)
                                      └── MongoDB Atlas    (replica set; one database per verified run)
```

## Environment variables

| Variable | Service | Required | Meaning |
|---|---|---|---|
| `APP_DATABASE_URL` | backend | yes | Application database, `postgresql+psycopg://user:pass@host/db?sslmode=require`. Empty falls back to a local SQLite file (development only). |
| `SANDBOX_ADMIN_DATABASE_URL` | backend | for verification | `postgresql://...` for a role that may `CREATE ROLE` and `CREATE SCHEMA` in a database **separate from the app database**. |
| `MONGO_URL` | backend | for verification | Atlas connection string (`mongodb+srv://...`). |
| `MONGO_SANDBOX_URL` | backend | recommended | Atlas user limited to `readWrite` on `run_*` databases. Falls back to `MONGO_URL`. |
| `CORS_ORIGINS` | backend | yes | Comma separated list; set to the Vercel URL. |
| `ANTHROPIC_API_KEY` | backend | no | Enables the AI advisory opinion. Without it a deterministic mock is used and the UI says so. |
| `LLM_MODEL` | backend | no | Default `claude-opus-5-5`. |
| `SENTRY_DSN` | backend | no | Enables Sentry when `sentry-sdk` is installed. |
| `LOG_LEVEL` | backend | no | Default `INFO` (JSON logs with `request_id` / `run_id`). |
| `DB_AUTO_CREATE` | backend | no | `false` in the image (Alembic owns the schema). |
| `PORT` | backend | set by host | Listen port, default 8000. |
| `VITE_API_URL` | frontend build | yes | Public backend URL, for example `https://schemashift-api.onrender.com`. |

Never commit `.env`. All secrets live in the host's environment settings.

## 1. Databases

**Neon (PostgreSQL).** Create a project and two databases, `schemashift_app` and `schemashift_sandbox`.
In the sandbox database create the admin role used for verification:

```sql
CREATE ROLE sandbox_admin LOGIN PASSWORD '<strong password>' CREATEROLE;
GRANT CREATE ON DATABASE schemashift_sandbox TO sandbox_admin;
```

Use the pooled connection string for the app database and the direct connection string for the
sandbox (it uses session-level settings such as `statement_timeout`). Keep the roles separate: the
sandbox role must have no access to the app database.

**MongoDB Atlas.** The free M0 cluster is a replica set, so transactions work. Create a database
user with `readWrite` on the `run_*` databases only (a custom role with a pattern privilege is not
available on M0; use `readWriteAnyDatabase` on M0 and tighten when upgrading) and allow the Render
egress IPs (or `0.0.0.0/0` for a demo).

## 2. Backend on Render

1. New > Blueprint, pick the repository. `render.yaml` defines the Docker web service
   (`backend/Dockerfile`, build context = repository root, health check `/api/v1/health`).
2. Fill in the environment variables above.
3. Deploy. The container runs `alembic upgrade head` and then `uvicorn --workers 2`, so migrations
   apply on every deploy. To run them by hand: `alembic upgrade head` from `/app`.
4. Render sends SIGTERM on redeploy. Uvicorn drains in-flight requests and the run manager finishes
   running jobs before exit. Leftover sandboxes (crashes) are removed by the janitor
   (`verification.sweep`) the next time a verified run starts.

Railway / Fly.io: create the service from `backend/Dockerfile` with the same variables. On Fly set
`internal_port = 8000` and the health check path `/api/v1/health`.

## 3. Frontend on Vercel

1. Import the repository, set the root directory to `frontend`.
2. Framework preset: Vite. Build `npm run build`, output `dist`.
3. Set `VITE_API_URL` to the backend URL. `vercel.json` rewrites all paths to `index.html` so
   `/runs/:id` links work.
4. Add the resulting URL to the backend's `CORS_ORIGINS`.

## 4. Docker images

On every push to `main`, CI builds `backend/Dockerfile` and pushes
`ghcr.io/<owner>/<repo>/backend:latest` and `:<sha>`. Any host that can run a container image can
deploy from there.

Local production-like run:

```powershell
docker build -f backend/Dockerfile -t schemashift-backend .
docker run --rm -p 8000:8000 -e APP_DATABASE_URL=sqlite:////tmp/app.db schemashift-backend
```

## 5. Security checklist

- [ ] `.env` is not in git history; `git log --all -- .env` is empty.
- [ ] `CORS_ORIGINS` lists only the frontend origin.
- [ ] Sandbox PostgreSQL role is separate from the app database role and has no access to it.
- [ ] Sandbox Mongo user limited to `run_*` databases (when the plan allows).
- [ ] Limits active: body 200 KB, 50 tables, 30 queries, 30 compiles/min and 5 verified runs/min per IP.
- [ ] Verified runs only through `POST /api/v1/runs` (never raw user SQL is executed; DDL is re-emitted from the AST, data uses bound parameters).
- [ ] Container runs as the non-root user `app` (uid 10001).
- [ ] `ANTHROPIC_API_KEY` set only on the backend; seed data is never sent to the model.
- [ ] Health check green: `GET /api/v1/health` returns `status: ok` with `database: up`.

## Known limits

- Rate limits are per worker and in memory; with two workers the effective limit is up to twice
  the configured value. A shared store (Redis) is the documented follow-up.
- Runs execute in an in-process pool of two threads; a restart marks interrupted runs as pending.
