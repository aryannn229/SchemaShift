# Phase 17: transaction and concurrency guarantee analyzer

## What changed
- **Scenarios and rules** (`concurrency/scenarios.py`, `equivalence/rules/concurrency.py`): five scenario families derived from the guarantee
  IR and the plan (FK race, duplicate insert, array writes, multi-collection transactions, counters) judged by `EQ-CONC-*` rules in the
  registry with evidence levels. They are not IR nodes, so the labeled corpus and its metrics are unaffected; the API returns them as
  `concurrency_verdicts` (own counts and overall), the UI as a second scope on the Verdicts tab and a Summary card.
- **Static analysis of the helpers** (`concurrency/helpers.py`): `ast`-based profile of `runtime_helpers.py` (reads, writes, lock calls,
  atomic guards). The rules decide from the shipped code; a test removes a safeguard from the source and checks that the verdict changes.
- **Codegen fixes** (`codegen/runtime_helpers.py`, `codegen/helpers.py`): `lock_row` lock documents inside transactions for child insert,
  parent delete and key update (closes a write-skew orphan race); embedded-array uniqueness folded into one atomic conditional push
  (`$not`/`$elemMatch`); `insert_<table>_tx` wrappers; `GUARDS` switch for the probes. `run_in_transaction` documents the retry contract
  (`with_transaction` retries `TransientTransactionError` and `UnknownTransactionCommitResult`).
- **Concurrency probes** (`verification/concurrency.py`): race writers against the replica set (barrier start, seeded jitter), once with the
  helpers as shipped and once unguarded, and report violations (orphans, duplicates, repeated ids, lost updates) with seed, rounds and
  duration. Scenarios that cannot be probed are listed with the reason.
- **UI**: concurrency scope on the Verdicts tab, probe table per verdict (strategy, writers, rounds, violations, seed), skip reasons.
- **Docs**: `docs/CONCURRENCY.md`, RULES.md (new rules and evidence), VERIFICATION.md, SPEC section 20, DECISIONS.

## Results (commands run 2026-10-22; PostgreSQL 16 + MongoDB 7 replica set)
Local probe run on a 3-table schema (seed 3, 60 rounds): FK race 0 orphans generated vs 60 of 60 unguarded; embedded-array duplicates 0 vs 60
of 60; counter 0 duplicate ids vs 86 of 120; transaction probe 0 lost updates (callback retried 127 times for 60 updates) vs 90 lost updates.
Integration test (40 rounds): generated violations are zero for every scenario kind; every unguarded variant shows violations (> 0).
- `pytest` unit: 1,128 passed. `pytest -m integration`: 93 passed. ruff, `ruff format`, `mypy --strict` clean; `rules_doc --check` up to date.
- Frontend: tsc, eslint, Vitest 55 tests, vite build; Playwright with the databases: 12 passed (adds the concurrency flow).

## Known limitations
- Zero violations is test evidence, not proof, and does not remove a verdict's conditions (replica set, helpers used inside
  `run_in_transaction`, an idempotent callback).
- Lock documents serialize writers touching the same parent (correctness over throughput); gaps in `next_id` when called outside the session.
- Nested arrays more than one level deep have no atomic guard (rule outcome ARRAY_TXN / ARRAY_RACE; the probe reports the scenario as skipped).
- Probe scenarios need integer or text keys that can be varied; others are reported as skipped.
- A verified run now takes about 25 s locally (counterexamples + 30 concurrency rounds per probe).
