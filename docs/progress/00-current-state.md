# Current state (before Phases F and 14-27)

Branch `roadmap/phases-14-27`, cut from `main` at 11c5bf9. Measured on 2026-10-22.

## Baseline (commands actually run)
- `cd backend; .venv/Scripts/python -m pytest -q` -> 891 passed, 58 deselected (integration/live).
- `ruff check .` clean; `mypy schemashift` clean (88 source files, strict).
- Unit-test line coverage per package (`pytest --cov=schemashift`): parser 96.1, semantic 99.4, ir 98.2,
  equivalence 99.4, optimizer 99.3, models 100, ai 98.9, api 91.8, metrics 82.8, **codegen 79.3**,
  **verification 60.5**, cli 41.6. Codegen and verification are completed by the integration suite
  (needs `docker-compose.test.yml`); the unit-only number is below the 85% target for codegen.

## How things work
- **Rule registry** (`equivalence/registry.py`): `@rule(node_type, rule_id, target)` registers a small function
  returning `(outcome_key, variables)`. Verdict text lives in declarative `Outcome`s (status, reason/mitigation
  templates, mongo_feature). A `Trace` records each checked condition for the "Why?" panel. `rules_doc.py`
  renders `docs/RULES.md` from the registry; CI fails if stale. `check_both` runs a placement-independent
  pass and a final pass with the plan.
- **Optimizer**: `features.py` extracts per-relationship features; `cost_model.py` computes a soft score
  from `weights.yaml` (read_together 0.30, shape 0.20, guarantees 0.20, independent_access -0.20,
  write_frequency -0.10; embed when >= 0.5) after hard constraints (4 MB ceiling, M:N, multi-parent,
  >1000 children, depth 3). `planner.py` resolves the global plan. Workload input today is only the
  supplied queries and `AccessHint` overrides.
- **Verification** (`verification/`): per-run PG role+schema and Mongo db, seed, generated schema/indexes/
  migration, then each query runs on PG (AST re-emitted) and as a pipeline on Mongo; `normalize.py` maps
  values to a canonical form (Decimal for all numbers, UTC ms datetimes, ObjectId->str, Binary subtype 4->UUID);
  `diff.py` compares ordered or as multisets with a 1e-9 tolerance and names known hypotheses. `probes.py`
  runs guarantee probes (cascade, SET NULL, RESTRICT, RI, key update).
- **Runs** (`api/runs.py`): `AppState` owns a 2-worker `ThreadPoolExecutor`; `POST /runs` persists a row and
  submits `_execute`; status is polled via `GET /runs/{id}`. Rate limits are in-memory per worker.
- **Frontend**: React + Vite + Tailwind (`darkMode: "class"`), ad-hoc components (`Badge`, `States`, tabs),
  generated OpenAPI client committed.

## Conflicts between the roadmap and the code
- Roadmap says "RULES.md" at the root; it is `docs/RULES.md`.
- `metrics/` and `cli.py` are described as pure in SPEC but `metrics` already calls verification; Phase 15 extends it
  under the same allowance.
- Phase F wants an "UNVERIFIED" grey badge and evidence levels, which only exist from Phase 16; Phase F builds
  the badge component now and wires it up in 16.
- Phase 21 deployment cannot be done from here (hard limit): configs, docs and a local prod-like check only.
- The roadmap's "wait for go" rule is overridden by the autonomous-execution prompt.
- This machine already runs other projects on 5432/8000/5173; SchemaShift's compose uses 5434/8001/5180 via env.
