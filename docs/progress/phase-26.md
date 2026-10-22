# Phase 26: SMT-backed constraint reasoning (Z3)

## What changed
- `schemashift/equivalence/smt/`: SQL constraints (NOT NULL, type ranges, NUMERIC precision and rounding, integer overflow, CHECK with three-valued logic) and the generated
  `$jsonSchema` / `$expr` validators (BSON null ordering, expressions that raise) as Z3 formulas, two queries per table. UNSAT/UNSAT marks the encoded verdicts PROVEN (assumptions
  stay on the verdict); SAT gives a concrete row and raises the status; an unsupported construct or a timeout (2,000 ms) is UNVERIFIED, never SAFE. Fragment and exclusions: SPEC.md section 27.
- API `smt` field and a "Constraint proofs (Z3)" card in the Summary tab; PROVEN badges appear in the Verdicts tab.

## Soundness test (`tests/integration/test_smt_soundness.py`, hypothesis, real PostgreSQL 16 and MongoDB 7)
Random single-table schemas inside the fragment; every counterexample row is inserted into both databases and must behave as claimed; for every proof, random rows must never be
accepted by one database and rejected by the other. Recorded runs (2026-10-23): 40 examples (default), 400, 1,500 and **1,000 examples: 1,000 tables, 0 unsupported, 0 timeouts,
863 counterexamples confirmed, 565 proofs checked against 33,900 random rows, 0 contradictions.**

## Bugs it found (all fixed, pinned by unit tests; docs/SMT.md)
1. `x IS NOT NULL` encoded as `IS NULL` (sqlglot `Is(..., negate=True)`), 2. integer arithmetic overflow (PostgreSQL raises, MongoDB does not), 3. NUMERIC rounds before CHECKs run,
4. **code generator**: contradictory `IN` lists produced `enum: []`, which MongoDB refuses, 5. **code generator**: `$strLenCP` guards raised on missing fields, rejecting valid rows.
- Unit: `tests/unit/equivalence/test_smt.py` (18 tests).
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).
- Corpus metrics unchanged by the SMT step (detection 100%, false positives 0%, 32 schemas, 883 labeled guarantees).
- Over 41 schemas the step moves PROVEN verdicts from 864 to 1,384 (UNVERIFIED stays 93) and finds counterexamples in 64 of 232 tables (`schemashift evaluate --all`).

## Found by Playwright, not by the soundness test
Z3's default context is not thread safe: two simultaneous compiles on the API's thread pool crashed the server (access violation inside `libz3`). `run_smt` now holds a process-wide lock;
`test_concurrent_compiles_do_not_crash_the_process` runs 8 threads x 6 compiles and fails with "Context mismatch" errors without it.

## Known limitations
- Row-local constraints on root tables inside the documented fragment only; embedded tables, UNIQUE, FOREIGN KEY, FLOAT, dates, UUID, JSON, arrays, string ordering and more are UNVERIFIED.
- PROVEN means "equivalent as formulas under the stated assumptions"; the test samples the encoder, it does not prove it.
