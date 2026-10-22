# Phase 14: semantic mismatches, differential and metamorphic testing

## What changed
- **New package `schemashift/fuzz/`** (I/O allowed): `spec.py` (case models rendered to SQL), `strategies.py` (hypothesis
  strategies), `harness.py` (compile + sandbox + differential `select`), `relations.py` (metamorphic relations),
  `driver.py` (budgeted run, shrinking), `regression.py` (YAML reproducers, replay). CLI: `schemashift fuzz --cases N --seed S
  [--metamorphic] [--save-dir D]`. Nightly workflow `.github/workflows/fuzz-nightly.yml` (1000 cases, uploads
  reproducers on failure). Docs: `docs/FUZZING.md`.
- **13 real bugs found by the fuzzer and fixed** (each has a `tests/regressions/fuzz_<hash>.yaml`, a DECISIONS.md entry
  and a unit test pinning the pipeline shape): `LIMIT 0`; global aggregate over an empty input (the known gap, now closed);
  repeated ORDER BY key; **data loss when a nullable-FK child was embedded** (new optimizer hard constraint
  `optional_parent`); string collation (`CompileOptions.string_collation`); enum ordering/comparison (two shapes);
  INNER JOIN through a LEFT-joined embedded child; `$lookup` on possibly-absent keys (two shapes); dropped FK column of a
  LEFT-joined embedded table; NULL flag of computed sort keys; integer division.
- `(p) IS [NOT] NULL` over boolean expressions is now translated (`PUnknown`). The TLP relation depended on it and had
  been silently vacuous until the run summary started reporting skipped relations and untranslatable reasons.
- One limitation kept as a documented reproducer (`tests/regressions/known_limitations/`): punctuation ordering differs
  between libc and ICU collations. A test fails if it ever stops reproducing.

## Files touched (main)
`backend/schemashift/fuzz/*` (new), `codegen/{queries,predicate,collections,emit,__init__}.py`, `optimizer/{features,cost_model,planner}.py`,
`pipeline.py` (option), `cli.py`, `docker-compose.test.yml` (ports overridable), `frontend/openapi.json` + `schema.d.ts`
(regenerated), goldens under `tests/unit/codegen/golden/` (only the `$facet` shape and collation lines changed),
`tests/unit/fuzz/*`, `tests/unit/codegen/test_semantic_fixes.py`, `tests/integration/test_fuzz.py`, `tests/regressions/*`.

## Existing tests that changed, and why (none weakened)
- `tests/unit/optimizer/test_planner.py`: fixtures that embedded a **nullable** foreign key now declare it `NOT NULL`
  (their intent is cycles, multi-parent, sizes); 3 tests added for the new constraint.
- `tests/unit/codegen/test_queries.py`: finds `$group` inside the new `$facet` (helper `group_of`).
- `tests/unit/codegen/test_predicate.py::test_value_expressions`: it asserted the buggy integer division
  (`$divide`); now asserts `$trunc` for integer operands and `$divide` for floating ones.
- `tests/integration/test_translation_semantics.py`: the test that pinned the global-aggregate gap now asserts the fix.
- Golden snapshots regenerated (reviewed: `$facet` default row, collation in `createCollection`, split `$addFields`).

## Results (commands run on 2026-10-22, local Docker: PostgreSQL 16, MongoDB 7 replica set)
- `pytest` (unit): 953 passed. `pytest -m integration`: 75 passed (18 new fuzz tests). Combined unit + integration run: 1026
  passed; the 2 failures in that run are env-dependent tests that expect no `SANDBOX_ADMIN_DATABASE_URL`/`MONGO_URL` and
  pass in the normal unit run.
- `ruff check` and `ruff format --check`: clean. `mypy --strict schemashift`: clean (95 files).
- `schemashift fuzz --cases 500 --seed 14 --metamorphic`: **0 failures** (489 valid cases, 900 queries compared, 892 comparable,
  1734 relation checks run, 459 skipped as not applicable, 8 PostgreSQL-rejected queries skipped). Earlier seeds 1-11 (300
  cases each) are also clean after the fixes.
- `schemashift evaluate`: unchanged (100% / 0% / 100% on the self-labelled corpus); `rules_doc --check`: up to date.
- Coverage (line, `pytest --cov=schemashift`): unit only: parser 96.1, semantic 99.4, ir 98.2, equivalence 99.4,
  optimizer 99.3, codegen 80.8, fuzz 76.1. Unit + integration: codegen **93.2**, fuzz 94.8, verification 92.2, api 93.7.
  (`codegen/runtime_*.py` run only inside generated scripts, so the unit-only codegen figure is below 85; the CI gate
  counts the combined core packages at 87%.)

## Known limitations
- Strings that differ only in punctuation/whitespace may sort differently (above). Equality is exact.
- Not fuzzed on purpose (listed in docs/FUZZING.md): microsecond timestamps, division by a column, MIN/MAX over enums, bigint
  sums beyond 2^63.
- Fuzz-run time is dominated by sandbox creation (about 0.5 s per case); shrinking can take minutes.
- `$lookup` with a null local key still scans the foreign collection's null entries in the LEFT-join guard form.

## Open questions for the owner
- Default `string_collation="en"` assumes an en_US-style PostgreSQL database; Neon's default is `C.UTF-8` (byte order):
  decide the default for the deployed demo. Logged as `[AUTONOMOUS — REVIEW]`.
