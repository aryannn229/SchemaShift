# Optimizer: embed vs reference

`plan_placement(graph, program, queries, seed_queries, options) -> PlacementPlan` decides, for every
foreign-key relationship, whether the child lives **inside** the parent document (`EMBED`) or in its own
collection (`REFERENCE`), and for junction tables whether they fold into an **array of references**
(`REF_ARRAY`, key `m2n:<junction>`). The LLM never overrides it (Phase 8).

## Features (per relationship)
cardinality, `child_independent_access`, `read_together_ratio`, `child_write_frequency`,
`estimated_child_count_per_parent`, estimated child/parent document size, `child_has_other_parents`,
`guarantees_needing_embedding` (cascade/SET NULL/SET DEFAULT on the edge + transactions spanning both tables).
Every value records its source: `hint` > `workload`/`seed` > `default`.

## Hard constraints (override the score)
1. self-reference -> REFERENCE
2. more than 1000 children per parent -> REFERENCE (unbounded growth)
3. projected parent document (parent + children x size x 2) above 4 MB -> REFERENCE

## Soft score
`raw = 0.30*read_together + 0.20*shape + 0.20*min(guarantees,2)/2 - 0.20*independent_access - 0.10*write_norm`,
normalised to [0, 1] with the bounds implied by the weights; embed when `score >= 0.5`.
All weights live in `backend/schemashift/optimizer/weights.yaml`.

## Global planner
A table embeds in at most one parent (best score wins, ties alphabetical); embedding cycles are broken at the
lowest-scoring edge; nesting depth is capped at 3; nested root documents must stay under the ceiling; a pure
junction whose edge would embed becomes `REF_ARRAY` hosted by that parent. User overrides
(`relationship_overrides`) win over scores but never over structural impossibilities (reported in `warnings`).

## Workload, seed statistics and sensitivity (Phase 18)
- **Workload.** Each statement has a relative frequency: `-- @freq N` before the statement, or `query_freq` (`q1`, `q2`, ... in file
  order) in a sidecar `workload.yaml` / the API option `workload`; the sidecar wins; unlisted statements weigh `default_freq` (1).
  Access statistics (reads together, writes, independent reads) are frequency-weighted.
- **Seed statistics.** `INSERT ... VALUES` seed data gives measured children per parent (average, maximum, p95), the skew
  `(max - avg) / avg` and the average child row size. They replace the defaults for the unbounded-growth and document-size constraints.
- **Workload terms.** Additive penalties, so plans without workload or seed data keep their earlier scores:
  `parent_write_amplification` (parent writes x the share of the document budget the embedded children fill),
  `child_write_share` (the child's share of the writing statements) and `fanout_skew`. Weights live under `workload:` in `weights.yaml`.
- **Explanation.** `PlacementDecision.explanation` states the score against the threshold, the strongest contributors and the
  workload/seed facts that applied. Different workloads can give different explained plans for the same schema.
- **Sensitivity.** Every weight is moved by -20/-10/+10/+20% (one at a time, 9 weights), the plan is recomputed from the same features and
  flips are recorded: `fragile` (flips at 10%), `watch` (only at 20%), `stable`, `constrained` (hard constraint or override). Deterministic.
  `schemashift sensitivity schema.sql [-q queries.sql] [--seed seed.sql] [--workload workload.yaml]`; Pagila (31 decisions) takes ~3 s.
- Nullable foreign keys are never embedded (a child without a parent has no host document).
