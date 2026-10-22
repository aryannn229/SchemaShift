# Architecture

```
SQL text
  -> parser/        Schema + Queries + Diagnostics         (pure)
  -> semantic/      SchemaGraph + SEM diagnostics          (pure)
  -> ir/            IRProgram: entity + guarantee nodes     (pure)
  -> equivalence/   initial pass (all REFERENCE)            (pure)
  -> optimizer/     PlacementPlan (embed vs reference)      (pure)     [Phase 5]
  -> equivalence/   final pass with the plan                (pure)
  -> codegen/       validators, indexes, pipelines, scripts (pure)     [Phase 6]
  -> verification/  sandbox run on real PG + Mongo          (I/O)      [Phase 7]
  -> ai/            advisory placement opinion              (I/O)      [Phase 8]
  -> api/ + frontend/                                                  [Phase 9-11]
```

## Stage contracts
| Stage | Input | Output |
|---|---|---|
| `parser.parse` | SQL string | `ParseResult(schema_, queries, diagnostics)`; never raises on bad SQL |
| `semantic.analyze` | `Schema`, queries | `AnalysisResult(graph, diagnostics)` |
| `ir.build_ir` | `SchemaGraph`, queries | `IRProgram` (stable node ids, see IR.md) |
| `equivalence.check_program` | `IRProgram`, graph, `PlacementPlan`, `RuleOptions` | `EquivalenceReport` (verdicts, counts, overall) |
| `equivalence.check_both` | same | initial + final reports and `changed_by_placement` |

Compiler stages are pure functions over frozen pydantic models. Only `verification/`, `ai/` and
`api/` perform I/O. Rules are declarative (`equivalence/registry.py`); `docs/RULES.md` is generated.
Targets are scoped (`@rule(target="mongodb")`) so another database is a new rule pack.

## Packages added in Phases 14 to 27
| Package | Role | Pure? |
|---|---|---|
| `fuzz/` | differential and metamorphic fuzzing against PostgreSQL and MongoDB, minimal reproducers, regression store | I/O |
| `metrics/` | corpus provenance groups, per-class metrics, kappa, public-schema benchmark, report | I/O only for the benchmark |
| `concurrency/` | scenario derivation, `ast` analysis of the shipped helpers; probes live in `verification/concurrency.py` | pure |
| `optimizer/stats.py`, `sensitivity.py` | seed statistics, workload terms, weight sensitivity | pure |
| `aggopt/` | pipeline rewrite rules, bounded search, cost model; `verification/explain.py` checks it | pure |
| `bench/` | benchmark laboratory, CLI only | I/O |
| `evolve/` | ALTER subset, differ, ordered reversible steps, guarantee diff, generated incremental script | pure (the script runs on MongoDB) |
| `migrate/` | dry run, checkpointed run, manifest, quarantine, consistency checks, script runner | I/O |
| `risk/`, `simulate.py` | rule-based risk score, decision simulator | pure |
| `ci/` | policy check, text/JSON/SARIF/Markdown output | pure |
| `equivalence/smt/` | Z3 encoding of SQL constraints and generated validators | pure |
| `evaluation/` | `evaluate --all` | I/O |
| `api/runs.py`, `worker.py` | run executor, arq queue client and worker | I/O |
| `packages/lsp`, `packages/vscode` | optional editor support, separately packaged | I/O |

`compile_sql` order now: parse, semantic, IR, optimizer (workload, sensitivity), equivalence, concurrency, then the SMT step
(`equivalence/smt`) which can raise evidence to PROVEN or attach a counterexample. `codegen.generate_code` runs the pipeline optimizer on
every SELECT. The risk score is computed from a `CompileResult` plus the untranslatable queries and checks that codegen reports.
