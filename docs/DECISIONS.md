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
| 2026-10-08 | Workload features use the SELECTs that touch the child as denominator: `read_together_ratio` = share that also touch the parent; `child_independent_access` = share that do not. No child reads -> defaults (0.5 / 0.3) | The spec does not define the denominator; counting parent-only queries wrongly penalised embedding rarely-queried children. |
| 2026-10-08 | Multi-parent children keep only their best-scoring parent edge (planner conflict resolution) instead of a flat "child_has_other_parents -> REFERENCE" | The spec states both "-> REFERENCE" and "embed in at most one parent"; the second lets `order_items` embed in `orders` while still referencing `products`. |
| 2026-10-08 | Self-referencing relationships are always REFERENCE (hard constraint) | Embedding a hierarchy in itself is impossible. |
| 2026-10-08 | Pure junction (no payload) whose FK edge would embed becomes `REF_ARRAY` on key `m2n:<junction>` (host = embedding parent); junctions with payload embed through the FK edge like any child | Matches "array of refs" in the spec while keeping payload junctions (e.g. `order_items`) embeddable. |
| 2026-10-08 | Normalized embed score = (raw - raw_min) / (raw_max - raw_min) with bounds derived from the weights (min -0.3, max 0.7 by default) | Spec asks for a score in [0, 1] with threshold 0.5 but gives raw weights. |
| 2026-10-08 | `samples/` schemas are copies of the corpus realistic cases (+ a new blog); `options.json` carries demo hints/overrides | One source of truth for the schemas; the banking demo needs an unbounded-growth hint and a REF_ARRAY override. |
| 2026-10-08 | Values are preserved on migration: a single-column PK becomes `_id` with the same value; AutoIncrement only affects new rows (counter collection with `preserve_integer_ids`, else ObjectId) | Keeps FK values joinable without an id map and makes query translation trivial. |
| 2026-10-08 | SQL NULL = absent field; validators exclude `null`; results use `$ifNull` | One consistent NULL representation; partial unique indexes use `$exists: true`. |
| 2026-10-08 | Composite PKs keep a generated ObjectId `_id` (deterministic hash on migration) plus a compound unique index | Idempotent upserts for every table shape. |
| 2026-10-08 | Collections/indexes/queries are emitted as mongosh JS and Python; migration and enforcement helpers are Python only | They need psycopg and sessions; a second language adds little. |
| 2026-10-08 | Enforcement helpers are a data-driven runtime (`runtime_helpers.py` + generated `PLAN`) with thin generated per-table wrappers | One tested implementation; wrappers list the verdict ids they mitigate. |
| 2026-10-08 | Unique indexes on embedded rows are multikey and always partial (`$exists: true`); per-parent uniqueness gets no index | Parents without children would otherwise collide on `null`. |
| 2026-10-08 | `uniqueItems: true` on folded scalar reference arrays | Cheap partial mitigation of the pair-uniqueness verdict (no duplicates within one host document). |
| 2026-10-08 | Positional updates use named identifiers with `$exists` array filters for outer arrays, never bare `$[]` | `$[]` fails when some outer element lacks the inner array. |
| 2026-10-08 | RESTRICT is enforced by the delete helper for embedded children too; SET NULL/DEFAULT on an embedded child cannot keep the row (documented CHANGED) | Matches the verdicts exactly. |
| 2026-10-08 | The sandbox PostgreSQL role is created through a separate admin DSN (`SANDBOX_ADMIN_DATABASE_URL`); the Mongo sandbox user (readWrite on `run_*`) is a deployment concern, the code only uses a `run_<id>` database | Role/user creation needs privileges the application database role must not have. |
| 2026-10-08 | DML is compared by snapshotting *all* tables on both sides after each statement; after a mismatch MongoDB is re-migrated from PostgreSQL | Keeps statements independent so one cascade difference does not poison later results. |
| 2026-10-08 | PostgreSQL-generated values (identity ids, `now()`/expression defaults) are mirrored into MongoDB documents via `INSERT ... RETURNING` | The spec's id map: lets inserts be compared exactly instead of flagging every timestamp. |
| 2026-10-08 | When PostgreSQL rejects a DML statement the MongoDB translation is tried on a snapshot: both rejecting is a MATCH | Reports real behaviour (CHECK/validator agreement) instead of assuming MongoDB would accept. |
| 2026-10-08 | Guarantee probes cover cascade, SET NULL/DEFAULT, RESTRICT, referential integrity and key updates; parents are chosen so a probe exercises its own foreign key (RESTRICT picks one PostgreSQL refuses) | Other foreign keys on the same parent would otherwise mask the guarantee. |
| 2026-10-08 | A junction folded into its parent counts as embedded in that parent in the equivalence rules (cascade/RI/restrict toward the host become SAFE/CHANGED accordingly) | Found by the verification harness: deleting the host removes the folded rows, exactly like a cascade. |
| 2026-10-08 | Seed generation derives candidate values from simple CHECKs (IN lists, numeric ranges); other CHECKs are verified with an SQL evaluator and retried | Random text never satisfies `status IN ('draft','published')`. |
| 2026-10-08 | Global aggregates over empty input are a documented gap (PostgreSQL 1 row, MongoDB 0 rows); the diff names the hypothesis | Emulating it needs a `$unionWith` trick that hurts readability. |
| 2026-10-08 | Default `LLM_MODEL` is `claude-opus-5-5` with `output_config.effort = low`, no `thinking`/`temperature` parameters | Current default Opus; those parameters are rejected or unnecessary on it. Still overridable by env var. |
| 2026-10-08 | The mock advisor's opinion is stored but flagged `available=false` and excluded from the agreement rate | Spec: show "AI advisor disabled" without a key; agreement is only meaningful for a real model. |
| 2026-10-08 | `ai.prompts` imports the DDL helpers lazily | Avoids an import cycle through `verification` -> `codegen` -> `pipeline`. |
| API app DB | SQLite fallback when APP_DATABASE_URL is unset; JSONB on PostgreSQL via `with_variant` | Zero-setup dev and fast tests; production uses Neon + Alembic. |
| Run execution | In-process ThreadPoolExecutor (2 workers), polling via GET /runs/{id} | No queue infrastructure for a portfolio-scale deployment; graceful shutdown drains the pool. |
| Rate limits | In-memory sliding window per IP (per worker) | Good enough for abuse protection; a shared store is a documented follow-up. |
