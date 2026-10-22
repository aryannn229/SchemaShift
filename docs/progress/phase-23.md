# Phase 23: safe migration mode

## What changed
- `schemashift/migrate/`: `migrate plan | dry-run | run | resume | verify | status | apply | rollback`. See `docs/SAFE_MIGRATION.md`.
- Dry run reads only: counts, estimated time (sampled throughput) and storage, and the records that would fail the **server's own validator** (`$documents` + `$match`),
  with reasons down to the nested field. Writes nothing (the test asserts that not even the collections exist).
- `run`: keyset-paged batches, upsert by `_id`, manifest rewritten atomically (temp file, fsync, rename) after every batch; resumes automatically. Invalid records are quarantined whole
  (`_schemashift_quarantine`). `verify`: document counts, key coverage, reference coverage, embedded rows, sampled deep comparison.
- Incremental scripts run with a manifest; irreversible steps need `--i-understand-this-is-lossy` and take a snapshot first; MANUAL steps are never executed.

## Acceptance (`tests/integration/test_safe_migration.py`, 7 tests, real PostgreSQL and MongoDB)
The CLI runs in a child process that calls `os._exit(137)` after 3 batches (no cleanup). `migrate resume` completes: 40 of 40 customers, no duplicate ids, every consistency check passes.
Also: re-running a finished migration changes nothing; the dry run reports 4 invalid records and writes 0; quarantine keeps the whole document and the reasons; a script with a
manual step is refused without consent, then snapshots and skips it, rolls back its reversible steps; a script killed after 2 steps resumes.
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The source must not change while a table is copied. Embedded-row checks are skipped for collections with quarantined parents. Estimates come from the first batches.
