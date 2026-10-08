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
