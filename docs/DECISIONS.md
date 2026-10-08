# Decision Log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-08 | Use `pip` + `venv` instead of `uv` | `uv` not installed on the dev machine; spec allows fallback. `pyproject.toml` stays uv-compatible. |
| 2026-10-08 | Use `npm` instead of `pnpm` | `pnpm` not installed; simplest default. |
| 2026-10-08 | `just` plus `scripts/dev.ps1` as `make` replacement | Dev machine is Windows/PowerShell; `just` is not installed so a PowerShell script offers the same targets. |
| 2026-10-08 | Repo root is the project root (no nested `schemashift/` dir) | Repo already exists at github.com/aryannn229/SchemaShift. |
| 2026-10-08 | Proceed through phases without waiting for approval; each phase still ends with tests + push | Explicit user instruction overriding Working Rule 2's "wait for continue". |
| 2026-10-08 | `.gitattributes` forces LF | Windows dev, Linux containers/CI. |
| 2026-10-08 | All subqueries (not only correlated) are rejected as `UNSUPPORTED_SUBQUERY` | Distinguishing correlation needs name resolution; simplest default. Spec only requires correlated to be rejected. |
| 2026-10-08 | Statement splitting and spans use our own comment/string-aware scanner; sqlglot only parses single statements | sqlglot aborts the whole script on one error and its AST lacks end positions. |
| 2026-10-08 | `ALTER TABLE ... ADD ...` is rewritten to a synthetic `CREATE TABLE` for parsing | sqlglot falls back to an opaque `Command` for several ADD forms; reuse guarantees identical `ForeignKey` objects. |
| 2026-10-08 | Duplicate-table detection (`SEM006`) is emitted by the parser | `Schema.tables` is a dict, so the duplicate is invisible to later stages. |
| 2026-10-08 | `NormalizedType` gained `enum_name`; `CheckConstraint` stores `expression_sql` (re-parseable) instead of an AST dump; `Index` gained `where_sql`; `Schema` gained `enums` | Needed to model ENUM columns, partial indexes and serialize checks. |
| 2026-10-08 | Constraint spans are the whole column/table-element definition; unsupported queries use non-`Command` SQL functions list COUNT/SUM/AVG/MIN/MAX/CAST | Simplest highlight target for the UI. |
| 2026-10-08 | `ParseResult.schema_` (trailing underscore) | `schema` shadows a pydantic BaseModel attribute. |
| 2026-10-08 | `CrossEntityUniqueness` is emitted for the key of a detected junction table (tables = the two related entities) | Spec text is vague ("unique across a junction/derived shape"); a pair-uniqueness over two entities is the only SQL constraint that naturally spans entities. Verdict depends on whether the junction keeps its own collection. |
| 2026-10-08 | Every FK without an explicit ON DELETE yields `RestrictDelete(action="NO ACTION")` | Spec maps `RESTRICT`/`NO ACTION` to RestrictDelete and NO ACTION is SQL's default. |
| 2026-10-08 | Test files exempt from ruff E501 | Long SQL literals in tests are clearer unwrapped. |
| 2026-10-08 | Rule functions return `(outcome_key, vars)`; verdict text lives in declarative `Outcome` templates in the registry | Spec asks for rules as data and a generated RULES.md; the wrapper builds the `Verdict`. |
| 2026-10-08 | The "initial" equivalence pass uses an all-REFERENCE plan | Placement-independent baseline; the final pass uses the real plan and flags changed verdicts. |
| 2026-10-08 | `SetNullOnDelete`/`SetDefaultOnDelete` embedded -> CHANGED (child is deleted, not nulled) | The spec row contradicts itself (SAFE then "actually CHANGED"). |
| 2026-10-08 | A 1:1 child embedded as a sub-document keeps EntityUniqueness/ValueUniqueness enforceable (SAFE); only array (1:N) embedding is CHANGED | Unique indexes work on nested paths of a single sub-document but not across array elements. |
| 2026-10-08 | Composite PKs are enforced with a compound unique index (documents keep a generated ObjectId `_id`); single-column PKs become `_id` | Keeps references by natural key simple; spec asked to record the choice. |
| 2026-10-08 | New outcome `EQ-ENTITY-UNIQ/PK_FOLDED` (junction folded into an array of refs -> CHANGED) | The key of a folded junction is not enforceable by an index. |
| 2026-10-08 | `LIKE 'literal%'` on a column is treated as translatable (`$jsonSchema pattern`) | MongoDB can express it; the spec list was not exhaustive. Other functions stay untranslatable. |
| 2026-10-08 | `JoinSemantics.right_columns_projected` ignores columns that only appear inside aggregate functions | Aggregated right-side columns do not expose missing-vs-NULL fields. |
| 2026-10-08 | Corpus labels live in `expected.yaml` with `bulk` glob patterns plus per-node entries; `type:*`/`not_null:*` labels are optional, all other guarantee nodes must be labeled | Keeps ~880 labels maintainable while forcing the interesting nodes to be labeled. |
| 2026-10-08 | Junction detection also matches tables with a surrogate PK + UNIQUE(fk1, fk2) (e.g. `order_items`) | Follows the spec's definition literally; Phase 5 must allow embedding such junctions into one parent. |
