# Evaluation methodology

`schemashift evaluate --all` reproduces every number reported for the project with one command (it needs PostgreSQL 16 and
MongoDB 7 for the database sections; without them those sections say they were skipped and never reuse an old number). It
writes `docs/evaluation.json` and the generated block at the end of this file, and refreshes `docs/METRICS.md`. This file
explains where the numbers come from and what they do not show.

```
SANDBOX_ADMIN_DATABASE_URL=postgresql://... MONGO_URL=mongodb://...?directConnection=true \n  python -m schemashift.cli evaluate --all --fuzz-cases 100 --fuzz-seed 52 --smt-examples 150 --bench-scale 1000
```
Every run records the commit, Python and library versions, PostgreSQL and MongoDB versions, CPU count, RAM and every seed.

## Evidence kinds, kept apart
1. **Self-labeled corpus** (`backend/tests/corpus/`, 32 schemas). The labels were written by the author of the rules from MongoDB
   semantics. Agreement shows that the implementation is consistent with the documented rule table; it is not independent
   validation.
2. **Independent corpus** (`backend/tests/corpus_independent/`). Labels written blind by someone other than the rule author
   (`labeled_by` not the author, `labeled_blind: true`). Empty until a second person labels; produced with
   `schemashift corpus label-template`. Scored separately and never merged with (1).
3. **Result-set correctness** against a real PostgreSQL, on the bundled samples and on public schemas
   (`samples/external/`). PostgreSQL is the oracle, so no labels are needed. This is the strongest label-free evidence; its
   limits are the hand-written workloads and synthetic data.
4. **Optimizer vs AI advisor agreement** (raw rate and Cohen's kappa). The mock advisor is a deterministic rule of thumb and is
   reported as such; the real-model figure needs an API key.
5. **Mutation score** (mutmut on `equivalence/` and `optimizer/`): how many deliberate code changes the tests notice.

## Metrics
Problem detection (CHANGED or BROKEN vs SAFE) precision/recall/F1, per-class one-vs-rest precision/recall/F1 and the 3x3
confusion matrix, per label group. Severity accuracy. Kappa. Mutation score = killed / all mutants (mutants no test covers count
as survivors).

## What each section measures
| Section | Measures | Oracle |
|---|---|---|
| Checker accuracy | verdicts against hand labels | the author's labels (self-labeled) |
| Evidence levels | how many verdicts are PROVEN / CONDITIONAL / TESTED / UNVERIFIED, with and without the Z3 step | none: a count |
| Z3 soundness | counterexamples and proofs against the real databases on random schemas | PostgreSQL and MongoDB themselves |
| Fuzzing | differential (PostgreSQL vs generated MongoDB), metamorphic, optimizer-vs-original | PostgreSQL, relations between queries |
| Result-set correctness | samples verified on 200 synthetic rows per table | PostgreSQL |
| Concurrency probes | violations with the shipped helpers vs without them | counts of orphans, duplicates, lost updates |
| Pipeline cost model | Spearman rho between estimated and `explain("executionStats")` cost over every candidate pipeline | MongoDB's own statistics |
| Benchmarks | latency of the chosen plan vs all-REFERENCE vs all-EMBED, after a correctness check | the clock |
| Mutation score | how many deliberate code changes the unit tests notice | the mutants |

## Documented failure cases
- **The optimizer's plan is often not the fastest.** At 1,000 rows per table the chosen plan was the fastest on 1 of 6 timed queries; the all-EMBED
  plan was usually as fast or faster, and PostgreSQL was faster than MongoDB on every query measured. The optimizer decides on
  correctness-related risk (document size, growth, atomicity, independent access) and a heuristic cost, not on measured latency; the benchmark
  lab (`schemashift bench`) exists to expose exactly this.
- **Decimal precision.** MongoDB's `decimal` has no precision or scale, so a value that PostgreSQL rejects as overflow is accepted. Z3 produces the
  row for every `NUMERIC(p,s)` column; the verdict is CHANGED with the mitigation (an application check).
- **Empty `IN` intersections and `$strLenCP` guards** were validator bugs found by the Z3 soundness test and fixed (docs/SMT.md).
- **Lossy conversions in incremental migrations** (text to number, primary-key changes, layouts outside the automatic restructuring) are MANUAL
  steps with a recovery strategy, never executed by SchemaShift.
- **Nothing is claimed about performance at scale beyond the sizes in `docs/bench/`**: 100,000 rows per table on the blog sample, run on a
  laptop with other processes running.

## Threats to validity
- **Author-written rules and labels.** The corpus labels (32 schemas, 883 guarantees) were written by the person who wrote the rules. 100% agreement
  shows consistency. The independent (blind, second labeler) corpus is empty: it needs another person (`schemashift corpus label-template`).
- **Small and mostly synthetic data.** Four hand-written samples, 32 corpus schemas, five public schemas; seed data comes from a generator;
  workloads are hand-written. The random schemas of the Z3 test are single tables.
- **Heuristic cost models.** The optimizer's score and the pipeline cost model are heuristics. The pipeline model correlates with MongoDB's
  execution statistics (rho in the generated block) over only a few dozen candidate pipelines, because the code generator already emits good pipelines
  and few rewrites apply.
- **Benchmarks.** One machine, both databases in Docker on the same host, a laptop with background load, a small number of repetitions. Treat them as
  evidence about direction, not as figures to quote.
- **The mutation score is stale.** The recorded score (89.1%, 2026-10-22) predates the optimizer changes of Phase 18 and the SMT step; re-run
  `scripts/mutation.sh` (Docker) to refresh it.
- **Z3 proves formulas, not behaviour.** PROVEN means the two constraint sets are equivalent as formulas under the stated assumptions (validators
  installed, a SQL NULL stored as an absent field). The fragment is documented in SPEC.md section 27; the soundness test samples it, it does not prove the encoder correct, and
  it found five defects (three in the encoder) before the encoder stopped contradicting the databases.
- **Versions.** PostgreSQL 16 and MongoDB 7 only; string collation differences (ICU vs libc) for punctuation are known.

<!-- BEGIN GENERATED RESULTS -->
Generated by `schemashift evaluate --all` on 2026-10-23 (commit 2c8bcbd).

### Environment
Python 3.13.0 on Windows-11-10.0.26200-SP0; 20 logical CPUs, None GB RAM. PostgreSQL: PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2) on x86_64-pc-linux-gnu. MongoDB: 7.0.43. Libraries: sqlglot 30.24.0, z3 5.3.1, hypothesis 6.170.5, pymongo 4.19.0, psycopg 3.3.6. Seeds: fuzz=52, smt=0, bench=0, verification=0.

### Checker accuracy (self-labeled corpus)
32 schemas, 883 labeled guarantees: detection 100.0%, false positives 0.0%, severity accuracy 100.0%. The labels were written by the author of the rules, so this checks consistency, not independent correctness. Independent (blind, second labeler) schemas: 0. Optimizer vs mock advisor kappa: 0.4056104851690045.

### Evidence levels
Verdicts over 41 schemas. Without the SMT step: {'CONDITIONAL': 2116, 'PROVEN': 864, 'UNVERIFIED': 93}. With it: {'PROVEN': 1384, 'CONDITIONAL': 1596, 'UNVERIFIED': 93}. SMT outcome per table: {'EQUIVALENT': 94, 'COUNTEREXAMPLE': 64, 'UNSUPPORTED': 74}.

### Z3 soundness against the real databases
150 random schemas in 32.6 s: {'timeouts': 0, 'tables': 150, 'unsupported': 0, 'counterexamples_confirmed': 69, 'proofs': 102, 'rows_agreeing': 6120}. Passed: True.

### Differential and metamorphic fuzzing
100 cases, seed 52: 221 queries compared, 451 metamorphic checks, 8 optimizer checks, 0 failure(s) in 64.0 s. Bugs found to date: {'fixed': 14, 'open': 0}.

### Result-set correctness, concurrency probes, pipeline cost model
- **ecommerce**: 5/5 queries match PostgreSQL (100.0%); pipeline rho 0.5 over 5 candidates; optimizer's pick cheapest in 3/3; concurrency {'probes': 18, 'generated_violations': 0, 'unguarded_violations': 57}.
- **blog**: 4/4 queries match PostgreSQL (100.0%); pipeline rho 1.0 over 5 candidates; optimizer's pick cheapest in 3/3; concurrency {'probes': 9, 'generated_violations': 0, 'unguarded_violations': 25}.
- **banking**: 7/7 queries match PostgreSQL (100.0%); pipeline rho 0.6 over 6 candidates; optimizer's pick cheapest in 4/4; concurrency {'probes': 18, 'generated_violations': 0, 'unguarded_violations': 176}.
- **university**: 5/5 queries match PostgreSQL (100.0%); pipeline rho 0.8 over 4 candidates; optimizer's pick cheapest in 3/3; concurrency {'probes': 12, 'generated_violations': 0, 'unguarded_violations': 107}.
- Pooled pipeline-cost rho: 0.8069 over 20 pairs.

### Benchmarks (chosen plan vs all-REFERENCE vs all-EMBED)
- **ecommerce** at 1000 rows/table: chosen plan fastest on 1 of 3 timed queries; correctness {'chosen': 1.0, 'all_reference': 1.0, 'all_embed': 1.0}.
- **blog** at 1000 rows/table: chosen plan fastest on 0 of 3 timed queries; correctness {'chosen': 1.0, 'all_reference': 1.0, 'all_embed': 1.0}.
- Overall: chosen plan fastest on 1/6.

### Mutation score
{'score': 0.8914919852034525, 'killed': 1446, 'total': 1622, 'survived': 163, 'timeout': 13, 'paths': ['schemashift/equivalence/', 'schemashift/optimizer/'], 'command': 'scripts/mutation.sh 8', 'date': '2026-10-22'}
<!-- END GENERATED RESULTS -->
