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
