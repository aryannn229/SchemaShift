# Phase 18: workload-aware schema design

## What changed
- `-- @freq N` annotations and a sidecar `workload.yaml` (`query_freq`, `default_freq`); frequency-weighted access statistics.
- Seed statistics (`optimizer/stats.py`): children per parent (avg/max/p95), skew, child row size feed the constraints and score.
- Workload terms in the cost model (`parent_write_amplification`, `child_write_share`, `fanout_skew`), additive, weights in `weights.yaml`.
- `PlacementDecision.explanation` and `.fragility`; `optimizer/sensitivity.py` perturbs 9 weights by +/-10/20% and records flips.
- CLI: `schemashift sensitivity`, `compile --workload`; API/OpenAPI/TS client regenerated; Relationships tab shows explanation, fragility and flips.
- Nullable foreign keys are no longer embedded.
- Docs: SPEC 21, OPTIMIZER.md, DECISIONS (2 entries tagged `[AUTONOMOUS — REVIEW]`).

## Results (commands run 2026-10-22)
- `pytest tests/unit`: 1,135 passed. `pytest -m integration` (local PostgreSQL 16 + MongoDB 7): 93 passed.
- ruff, ruff format, `mypy --strict` clean. Frontend typecheck, eslint, Vitest 55 passed.
- Pagila: 31 decisions, 23 stable / 8 fragile, whole compile ~3 s.
- Golden plans (4) and the banking snapshot regenerated after review: scores shifted slightly, no decision flipped; banking `m2n:beneficiaries` host moved `accounts` -> `customers` (tie at 0.61).

## Known limitations
- Frequencies are relative, not measured; sensitivity varies one weight at a time (no interactions).
- No dedicated Vitest/Playwright case for the fragility display (react-flow is not rendered in jsdom).
