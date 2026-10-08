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
