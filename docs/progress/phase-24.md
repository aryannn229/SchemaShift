# Phase 24: migration risk score and decision simulator

## What changed
- `schemashift/risk/`: five categories from explicit rules (constraint loss, query incompatibility, migration complexity, runtime enforcement, concurrency), every finding
  linking to the verdict, relationship or query behind it; formula and reference points in SPEC.md section 25. In the compile response and the Summary tab.
- `schemashift/simulate.py` and `POST /api/v1/simulate`: compile as the optimizer decides and again with overrides, workload frequencies and constraint toggles (ALTER);
  returns placements (blocked overrides with the reason), verdict and evidence changes, index and validator changes, pipeline changes with estimated cost, risk before and after.
  The optimizer's plan is never modified; the answer is labeled "simulated plan".
- UI: Decision simulator in the Relationships tab with a "Reset to optimizer plan" button; Risk card in the Summary tab.

## Results
- `tests/unit/test_risk_simulate.py`: 11 tests (every point traces to a finding, the formula holds, determinism, untranslatable queries add exactly their points, a flipped relationship
  changes verdicts / indexes / pipelines / risk, a blocked override is reported).
- Playwright: `e2e/simulate.spec.ts` flips a relationship and asserts verdicts, indexes and risk update and that reset restores the plan; a constraint toggle appears as a removed
  guarantee; axe passes on the Compare page and the simulator in light and dark.
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The reference points of the score are judgement calls, documented so they can be argued with. The first version saturated every category at 100 and was recalibrated
  (generic conditions no longer count).
