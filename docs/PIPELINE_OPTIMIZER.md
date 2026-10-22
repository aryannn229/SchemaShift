# Aggregation pipeline optimizer (Phase 19)

`schemashift/aggopt/` rewrites the pipelines that codegen produces. It is pure (no database access); the only
I/O is `verification/explain.py`, which checks the cost model against MongoDB.

## Rewrites (each is a registered rule with a precondition)
| Rule | What it does | Precondition |
|---|---|---|
| AGG-MERGE-MATCH | adjacent `$match` become one | none (a conjunction is order independent) |
| AGG-MATCH-BEFORE-WRAP | filter before `$replaceRoot {alias: $$ROOT}` | every field read is under that alias, no whole-document read |
| AGG-MATCH-PUSHDOWN | `$match` before `$lookup` / `$unwind` / `$addFields` / `$sort` | the match reads no path the earlier stage writes (or an ancestor of it) |
| AGG-LOOKUP-ELIMINATE | drop a `$lookup` whose result is unused | no later read of the alias; an `$unwind` of it counts as a read because it changes the row count |
| AGG-PRUNE-PROJECT | `$project` of the paths the first `$group` reads | no whole-document read; paths with a kept ancestor are dropped to avoid collisions |
| AGG-TOPK | `$limit` before a 1:1 `$project`/`$addFields` after `$sort` | the stage in between is one-to-one |

A `$match` is never moved past `$group`, `$limit` or `$skip`. Index-friendly shapes: matches with disjoint plain keys are
merged into one flat document; `recommend_indexes` lists the indexes the final pipeline can use (leading `$match` fields,
foreign fields of `$lookup`).

## Search and cost
Bounded breadth-first search (at most 200 candidates, depth 12) over single rewrite applications; the cheapest candidate by
`estimate_cost` wins (ties: fewer rewrites). Cost = documents scanned (an indexed leading `$match` scans `docs x selectivity`)
+ `$lookup` work (log2 of the foreign collection if the foreign field is indexed, else a tenth of it) + documents handed from
stage to stage + a small width term. Heuristic constants live in `aggopt/cost.py`.

## Validation against MongoDB
`verify(...)` runs `explain("executionStats")` for every candidate of every SELECT on the loaded sandbox
(`VerificationReport.pipeline_explain`). Observed cost = documents examined + 0.1 x keys examined + documents returned by each
stage. The report holds Spearman rho (pooled and per query) and whether the chosen candidate was among the cheapest observed.

## Safety net
- `generate_code` and `verify` use the optimized pipelines, so every PostgreSQL-vs-MongoDB differential run exercises them.
- The fuzzer also runs each rewritten query's **unoptimized** pipeline on MongoDB and requires identical rows
  (`SelectOutcome.optimizer_check`, finding kind `optimizer`; `optimizer_checked` in the fuzz summary).
- Unit tests cover each rule's positive case and each documented non-case.

## What the measurements say (honest)
MongoDB already derives the fields a pipeline needs, so the prune `$project` never wins in `explain` and the cost model, which
charges the extra stage, does not pick it. On the four samples the only rewrite chosen is the filter in front of the alias wrap
(banking q2: observed cost 970 to 755). Few sample queries leave room for rewriting because codegen already places the
`WHERE` filter first; the optimizer matters more for hand-written pipelines and larger joins.
