# Phase 27: reproducible evaluation and write-up

## What changed
- `schemashift evaluate --all` (`schemashift/evaluation/`): corpus metrics (self-labeled and independent, kappa), evidence levels with and without Z3, the Z3 soundness test, fuzzing,
  result-set correctness, concurrency probes, pipeline cost correlation, benchmarks and the recorded mutation score. Each section says why it was skipped when a prerequisite is missing; no old
  number is reused. Records commit, library, PostgreSQL and MongoDB versions, CPU, RAM and every seed. Writes `docs/evaluation.json`, the generated block of `docs/EVALUATION.md`,
  `docs/METRICS.md` and the `evaluation` summary in `docs/metrics.json` (shown on the About page).
- `docs/EVALUATION.md`: methodology, what each section measures, documented failure cases, threats to validity. README: what is new, honest headline metrics, limitations, updated architecture
  diagram. About page: new stages and the evaluation cards.

## Results (`schemashift evaluate --all --fuzz-cases 100 --fuzz-seed 52 --smt-examples 150 --bench-scale 1000`, 2026-10-23, about 4 minutes)
See the generated block in `docs/EVALUATION.md`: 32 corpus schemas / 883 labeled guarantees at 100% detection and 0% false positives (self-labeled); 41 schemas for the evidence breakdown;
150 Z3 soundness schemas with 69 counterexamples confirmed and 102 proofs against 6,120 rows; 100 fuzz cases with 0 failures; 21 of 21 sample queries matching PostgreSQL;
concurrency probes 0 violations with the generated helpers vs 372 without them; pooled pipeline rho 0.81 over 20 pairs; chosen plan fastest on 1 of 6 timed queries.
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The mutation score (89.1%, 2026-10-22) is stale: re-run `scripts/mutation.sh` (Docker, long). Independent labels still need a second person. See "Threats to validity" in `docs/EVALUATION.md`.
