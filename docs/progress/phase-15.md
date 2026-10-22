# Phase 15: independent benchmark and honest metrics

## What changed
- **`samples/external/`**: five public schemas fetched and ported mechanically by `scripts/port_external.py` at a recorded
  upstream commit: Pagila (PostgreSQL License), Chinook (MIT), Northwind (Ms-PL), Supabase Slack clone (Apache-2.0), Graphile
  Starter (MIT). Each folder has `schema.sql`, `queries.sql` (hand-written workload) and a README with source URL, commit,
  license and porting notes. Only statements with no schema meaning are dropped; functions, triggers, views and partitioning stay,
  so the parser's diagnostics for them are part of the benchmark.
- **Label format**: `labeled_by` + `labeled_blind` in every `expected.yaml`; `schemashift corpus label-template` writes an empty
  label file (guarantees, the SQL that produced each, no verdicts) for a blind second labeler.
- **Metrics** (`metrics/groups.py`, `labels.py`, `agreement.py`, `external.py`, `report.py`): precision, recall, F1 and the
  confusion matrix for the self-labeled and the independent group, **separately**; optimizer-vs-advisor agreement with Cohen's
  kappa and the disagreement table; external-schema diagnostics inventory and result-set correctness; failure-case list;
  `schemashift corpus report [--verify] [--ai] [--write-docs]`; `GET /api/v1/metrics` and the About page show it.
- **Mutation testing**: `scripts/mutation.sh` (mutmut in a Linux container, scoped to `equivalence/` and `optimizer/`).

## Findings from running public schemas (all fixed, none hidden)
1. Verification inserted seed rows in schema declaration order, so Chinook (tables declared alphabetically) violated its own foreign
   keys. Fix: dependency order.
2. `ORDER BY ... LIMIT n` with ties at the cut-off returned different (arbitrary) members; the harness reported a mismatch with a
   misleading "numeric precision" hypothesis. Fix: a recorded allowance for the final tie group only.
3. Northwind's REAL (float4) arithmetic is single precision in PostgreSQL and double in MongoDB. Fix: tolerance 1e-5 only for
   queries that reference a REAL column, with a note on the result.

## Honest state of the evidence
- Self-labeled: 32 schemas, 883 guarantees, 100% on every class. Written by the rule author; **not independent**.
- Independent: **0 schemas** (needs a second person; see FINAL_REPORT). No independent claim is made.
- Public schemas, label-free: Chinook and Northwind verify (31/31 queries MATCH, DML included); Pagila, the Slack clone and Graphile
  Starter contain functions, triggers, views, RLS or partitioning that SchemaShift rejects, so they are reported, not verified.
- Optimizer vs mock advisor: 119/165 agree, kappa 0.42 (the mock is a rule of thumb, not an AI signal). Real model: not measured
  (no API key).
- Mutation score: see METRICS.md (filled when `scripts/mutation.sh` completes; blocked marker below if it did not).

## Results (commands run 2026-10-22)
- `schemashift corpus report --verify --write-docs` and `schemashift evaluate --verify --write-docs` (bundled samples 21/21).
- Unit: 1,028 passed; integration: 86 passed (the final counts, shared with phase 16); ruff and mypy --strict clean.

## Known limitations
- The failure-case list is empty on the self-labeled corpus; that says nothing about unseen schemas.
- External workloads and data are synthetic; Pagila/Graphile/Slack are not verifiable until the parser supports functions, triggers
  and RLS (out of scope).
- A first attempt at mutation testing bind-mounted the whole repository (with `node_modules`) into a container and wedged Docker
  Desktop's engine on this machine; Docker Desktop had to be restarted (every container, including other projects', was
  restarted). The script now streams a trimmed tarball and limits CPUs and memory.
