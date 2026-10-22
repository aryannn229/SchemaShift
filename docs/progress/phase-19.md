# Phase 19: cost-based aggregation pipeline optimizer

## What changed
- `schemashift/aggopt/` (pure): six registered rewrite rules, each with a stated precondition (`AGG-MERGE-MATCH`, `AGG-MATCH-BEFORE-WRAP`,
  `AGG-MATCH-PUSHDOWN`, `AGG-LOOKUP-ELIMINATE`, `AGG-PRUNE-PROJECT`, `AGG-TOPK`), a bounded breadth-first search (200 candidates, depth 12),
  a cost model (documents scanned, `$lookup` work, stage hand-offs, width) and index recommendations. See `docs/PIPELINE_OPTIMIZER.md`.
- `codegen/queries.py`: SELECT pipelines pass through the optimizer; `TranslatedQuery` keeps the original pipeline, the rewrites and the estimated costs.
  `generate_code` and `verify` use the optimized pipelines, so every PostgreSQL-vs-MongoDB comparison tests what ships.
- `verification/explain.py`: `explain("executionStats")` of every candidate on the loaded sandbox, Spearman rho (pooled and per query).
- Fuzz safety net: each rewritten query's **unoptimized** pipeline is run on MongoDB and must return the same rows (`optimizer_checked`, finding kind `optimizer`).
- UI: "Pipeline optimizer" panel in the Generated Code tab (before / after, rewrites, estimated cost, indexes it can use).
- Bug found by the new fuzz run and fixed: a NUMERIC column compared with a decimal literal sent a double (never equal to the stored Decimal128).
  Regression `tests/regressions/fuzz_7e9ed92281.yaml`.

## Results (commands run 2026-10-23)
- `pytest tests/unit/aggopt`: 18 passed (each rule's positive case and each documented non-case, determinism, cost ordering).
- `schemashift fuzz -n 300 -s 52 --metamorphic`: 300 cases, 659 queries compared, 1,097 metamorphic checks, 45 optimizer-vs-original checks, **0 failures**.
- `explain` on the four samples (200 rows per table): the only rewrite chosen is the filter in front of the alias wrap (banking q2, observed cost 970 to 755);
  pooled Spearman rho **0.81 over 20 candidate pipelines** (`evaluate --all`).
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The prune `$project` is never chosen: MongoDB already restricts the fields, `explain` showed the extra stage only adds cost, and the model now charges it.
- On the samples few rewrites apply because codegen already places the `WHERE` filter first; the correlation rests on a small number of candidates.
- Filters are not pushed past `$group` (HAVING cannot be pushed) and `$limit`/`$skip`.
