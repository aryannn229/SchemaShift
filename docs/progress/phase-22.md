# Phase 22: incremental schema migration compiler

## What changed
- `schemashift/evolve/` (pure): ALTER subset applier (`alter.py`), typed differ with rename candidates (`differ.py`), re-planning and ordered steps with dependencies,
  reversibility and rollback (`plan.py`), the guarantee diff, an executable Python script with its own runtime (`runtime.py`, `script.py`). See `docs/EVOLUTION.md`.
- `pipeline.compile_schema` compiles an already parsed schema (used for ALTER input).
- `POST /api/v1/diff`, `schemashift diff old.sql new.sql | --alter script.sql [--confirm-rename a.b=c] [--out dir]`, and the **Compare versions** page.
- Step classes: REVERSIBLE, REVERSIBLE_WITH_SAVED_DATA (`_evolve_backup`, `_archive_<name>`), IRREVERSIBLE (MANUAL with a recovery strategy). Nothing is ever deleted.

## Acceptance on real databases (`tests/integration/test_evolve.py`, 4 cases)
PostgreSQL executes the ALTER; MongoDB A = migrate(v1) + generated incremental script; MongoDB B = fresh migrate of the evolved PostgreSQL. Documents, indexes and
validators are identical, and `rollback()` restores A to its v1 snapshot. Cases: field operations including inside embedded arrays (add with default, drop, rename, widen a type),
new collection, index and CHECK changes, and embed to reference and reference to embed restructuring.
- Unit: 19 tests for the ALTER subset, differ, renames, plan classification, script validity; API and CLI: 3 tests; Vitest: 3 tests for the page.
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- Restructuring is automatic only for a child embedded directly in a root document with single-column keys; everything else is MANUAL.
- Backfills use literal defaults; text-to-number conversions keep values that do not parse. Concurrent writes during a migration are not handled.
