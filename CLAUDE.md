# CLAUDE.md

## Overview
SchemaShift is a SQL (PostgreSQL) to MongoDB migration compiler: parser -> semantic analysis -> IR ->
equivalence checker -> optimizer (embed vs reference) -> codegen -> verification harness, exposed via a
FastAPI backend and React frontend. Full requirements are in `SPEC.md`; decisions in `docs/DECISIONS.md`.

## Commands (Windows / PowerShell)
Task runner: `./scripts/dev.ps1 <target>` (or `just <target>` if installed).
- `setup`: create `backend/.venv`, install deps, `npm install`
- `dev`: `docker compose up --build` (postgres, mongo replica set, backend :8000, frontend :5173)
- `test`, `test-integration`, `lint`, `typecheck`, `build`, `migrate`, `evaluate`
Backend directly: `cd backend; .venv\Scripts\python -m pytest`, `-m ruff check .`, `-m mypy schemashift`.
Frontend: `cd frontend; npm test | npm run lint | npm run typecheck | npm run build`.

## Architecture
- `backend/schemashift/`: compiler stages are pure functions over frozen pydantic models.
  Only `verification/`, `ai/`, `api/` do I/O.
- API under `/api/v1` (health at `/api/v1/health`), docs at `/api/docs`.
- Config only via env vars (`api/settings.py`); see `.env.example`. Never commit `.env`.

## Conventions
- Full type hints, `mypy --strict`; TS `strict: true`.
- Conventional Commits; commit per unit of work; push at end of each phase after tests pass; never force-push.
- Tests mandatory; never weaken a test to pass. Rules are data (registries), not if/else sprawl.
- Never execute raw user SQL (SPEC Section 11).
- LF line endings (`.gitattributes`).

## Pipeline status (update as phases land)
- Phase 1 parser (`parser/`): `parse(sql) -> ParseResult(schema_, queries, diagnostics)`; never raises on bad SQL.
- Phase 2 semantic (`semantic/`): `analyze(schema, queries) -> AnalysisResult(graph, diagnostics)`; SEM001-SEM012.
- Phase 3 IR (`ir/`): `build_ir(graph, queries) -> IRProgram`; node docs in `docs/IR.md`.
- Phase 4 equivalence (`equivalence/`): rules are registered with `@rule(...)` + declarative `Outcome`s;
  `check_program` / `check_both`. `docs/RULES.md` is generated: `python -m schemashift.equivalence.rules_doc`
  (CI fails if stale). Add a rule => add outcomes, a branch case in `tests/unit/equivalence/test_rule_branches.py`,
  and regenerate RULES.md.
- Phase 5 optimizer (`optimizer/`): `plan_placement(...) -> PlacementPlan`; weights in `optimizer/weights.yaml`;
  docs/OPTIMIZER.md. `pipeline.compile_sql(schema, queries, seed, CompileOptions)` runs every pure stage.
- Phase 6 codegen (`codegen/`): `generate_code(result, options)`; the layout is the single source of truth for
  validators, indexes, query translation and migration; `runtime_migrate.py` / `runtime_helpers.py` are embedded
  verbatim in the generated scripts. Integration tests need `docker compose -f docker-compose.test.yml up -d --wait`
  and `pytest -m integration`. See docs/CODEGEN.md.
- Phase 7 verification (`verification/`): `verify(result, options, SandboxConfig)`; sandbox role+schema and Mongo db per run,
  seed -> generated schema/indexes/migrate -> SQL vs pipeline diff -> probes. Needs the docker-compose.test.yml services.
  See docs/VERIFICATION.md. Never execute user SQL strings: only AST re-emission and bound parameters.
- Never patch a file with `s[:i] + new + s[j:]` without asserting `j > i` (it silently duplicated a file once).
- Corpus (`backend/tests/corpus/*/expected.yaml`): hand-labeled ground truth; `schemashift evaluate` prints metrics
  (`--write-docs` updates `docs/METRICS.md`).
- Shell gotcha: do not run Python from inside `backend/schemashift/parser/` (its `types.py` shadows stdlib `types`).
- Phase 9-10 API (`api/`): routes under `/api/v1` (health, compile, runs, runs/{id}, runs/{id}/export, samples, metrics).
  `deps.get_state` lazily builds `AppState` (engine, session factory, advisor, DbCache, 2-worker run pool).
  Middleware: body limit (413), per-IP rate limit (429), security headers, structlog with request_id/run_id.
  App DB: SQLAlchemy models in `api/db/models.py`; Alembic in `backend/alembic` (`alembic upgrade head`;
  a test asserts migrations cover the models). Verification runs only via `POST /runs` with `verify:true`
  and needs SANDBOX_ADMIN_DATABASE_URL + MONGO_URL.
- Phase 11 frontend (`frontend/`): React + Vite + Tailwind (`darkMode: "class"`), Monaco editor, react-flow graph.
  API client is generated: `python scripts/gen_openapi.py` (from `backend/`, venv active) then `npm run gen:api`;
  `openapi.json` and `src/api/schema.d.ts` are committed and CI fails when stale. Never hand-write API types.
  Tests: `npm test` (Vitest), `npx playwright test` (E2E, runs in CI).
- Phase 12 metrics: `schemashift evaluate [--verify] [--ai] --write-docs` updates docs/METRICS.md and docs/metrics.json
  (served by GET /metrics). Sections not recomputed keep their previous values.
- Phase 13 deploy: `backend/Dockerfile` (build context = repo root), `render.yaml`, `frontend/vercel.json`,
  docs/DEPLOYMENT.md. The image runs `alembic upgrade head` then uvicorn with 2 workers.
- Python in bash heredocs: avoid `"\n"` inside generated code (it becomes a real newline); use the Edit tool.
