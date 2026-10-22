# Phase 16: explainable verification engine

## What changed
- **Evidence levels** on every verdict: `PROVEN` (stated argument), `CONDITIONAL` (structured conditions), `TESTED`, `UNVERIFIED`.
  One table (`equivalence/evidence.py`) holds level, argument and conditions per rule outcome; a test checks it is complete and
  consistent; `docs/RULES.md` shows it. Verdicts also carry `decided_by` (rule/outcome and the condition that fired) and `inputs`
  (IR node, the plan decision and score of the relationship, options consulted).
- **Evidence upgrades**: a verification run raises UNVERIFIED query-level verdicts to TESTED when their query MATCHes (separate
  `evidence_upgrades` list with the basis); nothing else is upgraded.
- **Minimal counterexamples** (`verification/counterexample.py`): smallest dataset (one row per needed table, NOT NULL ancestors,
  the cascade chain, two rows for a self-reference), operation on both databases, behaviour of each side and the resulting
  difference; or a **documented reason** (`GAP_REASONS`) when the difference is a property of the representation. Every BROKEN
  verdict on the four bundled samples has one or the other (integration test). Deterministic per seed.
- **API**: verdict fields in `/compile`; `counterexamples`, `counterexample_gaps`, `evidence_upgrades` on the verification report;
  `POST /api/v1/runs/{id}/counterexamples/replay?verdict_id=` re-runs one on fresh sandboxes.
- **Bug fixed on the way**: the "5 verified runs per minute" limit was never enforced (all POSTs used the compile bucket). Verified
  runs and replays now have their own limit, with tests.
- **Frontend** (design system components): evidence badge and filter in the Verdicts table; expanded row shows evidence, argument,
  conditions, decided-by and inputs, the Why? trace, and the counterexample with setup data, operation, PostgreSQL and MongoDB
  outcomes side by side, the resulting difference and a Replay probe button. Documented gaps are shown instead of a counterexample.

## Files touched (main)
`models/evidence.py` (new), `models/verdict.py`, `equivalence/{evidence,registry,rules_doc}.py`, `verification/{counterexample,render,
runner,probes}.py`, `api/{main,middleware,schemas}.py`, `api/routes/compile.py`, `docs/{RULES,VERIFICATION}.md`,
`frontend/src/components/{EvidencePanel,CounterexampleView,VerdictsTab,ResultTabs}.tsx`, `frontend/src/api/*`, tests.

## Results (commands run 2026-10-22; PostgreSQL 16 + MongoDB 7 replica set in Docker)
- `pytest` unit: 1,028 passed. `pytest -m integration`: 86 passed (counterexamples 9, API replay, external benchmark, fuzz,
  verification, codegen).
- `ruff check`, `ruff format --check`, `mypy --strict schemashift`: clean. `rules_doc --check`: up to date.
- Frontend: `tsc`, `eslint`, Vitest (52 tests), `vite build` clean; Playwright against a local API with databases: 11 passed
  (flows, axe on every tab in both themes, keyboard, the new counterexample/replay flow); visual snapshots refreshed.

## Known limitations
- Evidence `PROVEN` means "a stated argument", not formal verification. Validators are CONDITIONAL until Phase 26 can prove the
  faithful fragment.
- Counterexamples cover foreign-key actions, RESTRICT, orphan inserts and key updates. Uniqueness inside an array, defaults, types
  and generated ids have documented reasons instead; the folded-junction pair uniqueness shows a difference only under concurrent
  writers (Phase 17).
- One sandbox per counterexample (about 0.3 s each, at most 40 per run).
