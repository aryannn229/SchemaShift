# SchemaShift: Master Build Prompt for Claude Code

> **How to use this file:** Save it in the root of an empty repo as `SPEC.md`, open Claude Code in that folder, and say:
> *"Read SPEC.md fully. Follow the Working Rules section exactly. Start with Phase 0."*

---

## 0. Your Role and Working Rules (read first, follow always)

You are the lead engineer building **SchemaShift**, a SQL → MongoDB migration *compiler* with a semantic equivalence checker, a cost-based optimizer, an advisory AI layer, a verification harness, and a deployed web app. This is a university compiler-design project that will also be deployed publicly and expanded later, so code quality, test coverage, and clean architecture matter as much as features.

**Rules:**

1. **Work phase by phase**, in the order given in Section 9. Do not start a phase until the previous phase's acceptance criteria pass.
2. **At the end of every phase:** run the full test suite, run linters/type checks, then stop and give me a short summary: what was built, test results, any deviations from this spec, and open questions. Wait for my "continue" before the next phase.
3. **Create `CLAUDE.md`** in Phase 0 with: project overview, commands (run, test, lint, build), architecture summary, and conventions. Keep it updated as the project grows.
4. **Tests are mandatory.** Every module in `parser/`, `semantic/`, `ir/`, `equivalence/`, `optimizer/`, `codegen/` ships with unit tests. Target ≥ 85% line coverage on those packages. Never delete or weaken a test to make it pass; fix the code.
5. **Type everything.** Python: full type hints, `mypy --strict` clean on the backend core packages. Frontend: TypeScript `strict: true`.
6. **No silent scope creep.** If something in this spec is ambiguous or seems wrong, ask before inventing. If you must pick a default to keep moving, pick the simplest one and log it in `docs/DECISIONS.md` (date, decision, reason).
7. **Commit after each meaningful unit of work** with Conventional Commits (`feat(parser): ...`, `test(equivalence): ...`, `fix: ...`). One phase = several commits, never one giant one.
8. **Never execute user-supplied SQL as a raw string.** See Section 11 (Security). This is non-negotiable.
9. **Rules are data, not if/else sprawl.** The equivalence rule table and type map live in declarative structures (Python dataclasses/registries or YAML), and each rule's logic is a small, individually tested function.
10. **Keep compiler stages pure.** Parser → Semantic → IR → Equivalence → Optimizer → Codegen are pure functions over immutable data models (no DB, no network, no globals). Only `verification/`, `ai/`, and `api/` do I/O.

---

## 1. Product Summary

A user pastes a PostgreSQL schema (DDL) plus optional sample queries and seed data. SchemaShift:

1. **Parses** the SQL into a typed `Schema` model.
2. **Analyzes** it semantically (relationship graph, error detection).
3. **Lowers** it to an **Intermediate Representation (IR)** where each node encodes a *guarantee* (e.g., "deleting a parent deletes its children"), not syntax.
4. **Checks equivalence**: for each IR node, decides whether MongoDB can preserve that guarantee → **SAFE**, **CHANGED**, or **BROKEN**, with a plain-English reason and a suggested mitigation.
5. **Optimizes**: decides embed vs. reference for each relationship using a cost model (an LLM gives an advisory second opinion; it never overrides).
6. **Generates code**: MongoDB collections, `$jsonSchema` validators, indexes, translated queries (aggregation pipelines), and a data-migration script.
7. **Verifies**: runs original queries on PostgreSQL and generated queries on MongoDB with identical seed data and diffs the results.
8. **Reports** all of the above in a web UI, stores run history, and computes evaluation metrics.

---

## 2. Tech Stack (use exactly this unless I approve a change)

| Layer | Choice |
|---|---|
| Backend language | Python 3.11+ |
| SQL parsing | `sqlglot` (dialect: `postgres`) |
| Data models | `pydantic` v2 (frozen models for compiler stages) |
| Graph algorithms | `networkx` |
| API | FastAPI + Uvicorn |
| App DB ORM | SQLAlchemy 2.x + Alembic migrations |
| App DB | PostgreSQL (prod), SQLite allowed for unit tests only |
| Verification targets | PostgreSQL 16 + MongoDB 7 (single-node **replica set**, required for transactions) |
| Mongo driver | `pymongo` (sync is fine; wrap in threadpool from FastAPI) |
| Postgres driver | `psycopg` v3 |
| LLM | Provider-agnostic interface; default implementation uses the Anthropic API via `anthropic` SDK. Model name from env var `LLM_MODEL`. |
| Testing | `pytest`, `pytest-cov`, `hypothesis` (property tests), `testcontainers` (integration) |
| Lint/format | `ruff` (lint + format), `mypy` |
| Frontend | React 18 + TypeScript + Vite, Tailwind CSS, `@monaco-editor/react`, TanStack Query, React Router |
| Frontend tests | Vitest + React Testing Library; Playwright for 1–2 E2E flows |
| Packaging | `uv` (or Poetry if `uv` unavailable) for Python, `pnpm` for frontend |
| Containers | Docker + docker-compose |
| CI | GitHub Actions |

---

## 3. Repository Structure

```
schemashift/
├── SPEC.md                      # this file
├── CLAUDE.md                    # living guide for Claude Code
├── README.md                    # public-facing: what, why, demo GIF, quickstart
├── docker-compose.yml           # postgres, mongo (rs), backend, frontend
├── docker-compose.test.yml
├── .env.example
├── .github/workflows/ci.yml
├── docs/
│   ├── ARCHITECTURE.md          # pipeline diagram + stage contracts
│   ├── IR.md                    # every IR node, fields, semantics
│   ├── RULES.md                 # every equivalence rule + rationale (auto-generated from rule registry)
│   ├── SUPPORTED_SQL.md         # exact supported subset + explicitly rejected constructs
│   ├── DECISIONS.md             # decision log
│   └── METRICS.md               # how metrics are computed + latest numbers
├── backend/
│   ├── pyproject.toml
│   ├── schemashift/
│   │   ├── __init__.py
│   │   ├── models/              # shared pydantic models (Schema, Column, IR nodes, Verdict...)
│   │   ├── parser/
│   │   │   ├── ddl.py           # CREATE TABLE / ALTER TABLE ADD CONSTRAINT / CREATE INDEX
│   │   │   ├── queries.py       # SELECT / INSERT / UPDATE / DELETE subset
│   │   │   ├── types.py         # SQL type normalization
│   │   │   └── errors.py        # ParseError with line/col
│   │   ├── semantic/
│   │   │   ├── graph.py         # relationship graph (networkx DiGraph)
│   │   │   ├── cardinality.py   # infer 1:1, 1:N, M:N (junction table detection)
│   │   │   ├── validators.py    # each check is a separate function
│   │   │   └── diagnostics.py   # Diagnostic(severity, code, message, location)
│   │   ├── ir/
│   │   │   ├── nodes.py         # IR node classes
│   │   │   ├── builder.py       # SchemaGraph -> IRProgram
│   │   │   └── printer.py       # human-readable IR dump (for debugging + UI)
│   │   ├── equivalence/
│   │   │   ├── registry.py      # rule registry (decorator-based)
│   │   │   ├── context.py       # RuleContext: placement decisions, Mongo version, options
│   │   │   ├── rules/           # one file per IR node family
│   │   │   ├── checker.py       # runs rules, produces EquivalenceReport
│   │   │   └── rules_doc.py     # generates docs/RULES.md from registry
│   │   ├── optimizer/
│   │   │   ├── features.py      # feature extraction per relationship
│   │   │   ├── cost_model.py    # scoring
│   │   │   ├── planner.py       # global plan, conflict resolution, cycle breaking
│   │   │   └── workload.py      # optional access-pattern hints from queries
│   │   ├── codegen/
│   │   │   ├── collections.py   # collection + $jsonSchema validator generation
│   │   │   ├── indexes.py
│   │   │   ├── queries.py       # SQL SELECT -> aggregation pipeline
│   │   │   ├── migration.py     # data migration script (Postgres -> Mongo)
│   │   │   └── emit.py          # emitters: mongosh JS, Python (pymongo), Mongoose (later)
│   │   ├── verification/
│   │   │   ├── sandbox.py       # ephemeral PG schema + Mongo DB per run
│   │   │   ├── seed.py          # synthetic seed data generator (respects constraints)
│   │   │   ├── runner.py        # run both sides
│   │   │   ├── normalize.py     # result normalization
│   │   │   └── diff.py          # result-set comparison
│   │   ├── ai/
│   │   │   ├── base.py          # LLMAdvisor protocol
│   │   │   ├── anthropic_advisor.py
│   │   │   ├── mock_advisor.py  # deterministic, used in tests
│   │   │   ├── prompts.py
│   │   │   └── cache.py
│   │   ├── metrics/
│   │   │   └── evaluate.py      # computes the 4 metrics over the labeled corpus
│   │   ├── pipeline.py          # orchestrates the whole compile, returns CompileResult
│   │   ├── cli.py               # `schemashift compile file.sql` (Typer)
│   │   └── api/
│   │       ├── main.py
│   │       ├── routes/          # compile, runs, verify, health, samples
│   │       ├── schemas.py       # request/response DTOs
│   │       ├── deps.py
│   │       ├── db/              # SQLAlchemy models + session
│   │       └── settings.py      # pydantic-settings, all config from env
│   ├── alembic/
│   └── tests/
│       ├── unit/                # mirrors package layout
│       ├── integration/         # testcontainers: PG + Mongo
│       ├── corpus/              # labeled test schemas (see Section 10)
│       └── conftest.py
├── frontend/
│   ├── src/
│   │   ├── api/                 # typed client generated from OpenAPI (openapi-typescript)
│   │   ├── components/
│   │   │   ├── SqlEditor.tsx
│   │   │   ├── VerdictTable.tsx
│   │   │   ├── VerdictBadge.tsx
│   │   │   ├── RelationshipGraph.tsx   # react-flow visualization
│   │   │   ├── PlacementPanel.tsx      # embed/reference decisions + AI opinion
│   │   │   ├── CodeOutput.tsx          # tabs: validators, indexes, queries, migration
│   │   │   ├── VerificationPanel.tsx
│   │   │   └── DiagnosticsList.tsx
│   │   ├── pages/
│   │   │   ├── Home.tsx         # editor + run
│   │   │   ├── RunDetail.tsx    # /runs/:id shareable report
│   │   │   ├── History.tsx
│   │   │   └── About.tsx        # how it works
│   │   └── main.tsx
│   └── tests/
└── samples/                     # demo schemas shown in the UI "Load sample" dropdown
    ├── ecommerce/               # schema.sql, queries.sql, seed.sql (optional)
    ├── blog/
    ├── university/
    └── banking/                 # deliberately includes BROKEN constructs
```

---

## 4. Core Data Models (`schemashift/models/`)

All compiler-stage models are **frozen pydantic models**. Every model that comes from source SQL carries a `SourceSpan(line_start, col_start, line_end, col_end)` so the UI can highlight the offending SQL.

```python
class Column:            name, sql_type: NormalizedType, nullable, default: DefaultExpr | None,
                         is_identity (SERIAL/IDENTITY), span
class PrimaryKey:        columns: tuple[str, ...], span
class ForeignKey:        name | None, columns, ref_table, ref_columns,
                         on_delete: RefAction, on_update: RefAction, deferrable: bool, span
class UniqueConstraint:  name | None, columns, span
class CheckConstraint:   name | None, expression: sqlglot AST (serialized), span
class Index:             name, columns, unique, span
class Table:             name, columns, primary_key, foreign_keys, uniques, checks, indexes, span
class Schema:            tables: dict[str, Table], dialect="postgres"
class Query:             id, kind (SELECT/INSERT/UPDATE/DELETE), ast, raw_sql, span
class TransactionBlock:  id, statements: tuple[Query, ...], span   # BEGIN ... COMMIT
RefAction = Literal["NO ACTION","RESTRICT","CASCADE","SET NULL","SET DEFAULT"]
```

`NormalizedType` = `{ base: Literal[...], length, precision, scale, is_array }`, where `base` is one of `INTEGER, BIGINT, SMALLINT, DECIMAL, FLOAT, DOUBLE, BOOLEAN, TEXT, VARCHAR, CHAR, DATE, TIMESTAMP, TIMESTAMPTZ, TIME, UUID, JSON, JSONB, BYTEA, ENUM, INTERVAL`.

---

## 5. Pipeline Stages: Detailed Spec

### Phase 1: Parser

**Supported (must work):**
- `CREATE TABLE` with column constraints (`PRIMARY KEY`, `NOT NULL`, `UNIQUE`, `DEFAULT`, `REFERENCES ... ON DELETE/UPDATE ...`, `CHECK`) and table constraints (composite PK, composite FK, composite UNIQUE, named `CONSTRAINT x ...`).
- `SERIAL`, `BIGSERIAL`, `GENERATED ... AS IDENTITY`.
- `ALTER TABLE ... ADD CONSTRAINT` (FK, UNIQUE, CHECK, PK).
- `CREATE [UNIQUE] INDEX`.
- `CREATE TYPE ... AS ENUM`.
- Queries: `SELECT` with `WHERE`, `INNER/LEFT JOIN` on equality, `GROUP BY`, aggregates (`COUNT, SUM, AVG, MIN, MAX`), `HAVING`, `ORDER BY`, `LIMIT/OFFSET`, `DISTINCT`, simple `IN`/`BETWEEN`/`LIKE`/`IS NULL`. `INSERT ... VALUES`. `UPDATE`/`DELETE` with simple `WHERE`.
- `BEGIN; ... COMMIT;` blocks → `TransactionBlock`.

**Explicitly rejected with a clear `UnsupportedConstruct` diagnostic (not a crash):** triggers, stored procedures/functions, views, correlated subqueries, window functions, CTEs (recursive or not, for v1), `RIGHT/FULL OUTER JOIN`, table inheritance, partitioning, exclusion constraints, `LATERAL`. List all of them in `docs/SUPPORTED_SQL.md`.

**Requirements:**
- Multi-statement input; one bad statement yields a diagnostic for that statement and parsing continues for the rest.
- Errors include line/column.
- Column-level `REFERENCES` and table-level `FOREIGN KEY` produce identical `ForeignKey` objects.
- Identifier normalization: unquoted → lowercase; quoted → preserved.

**Acceptance:** ≥ 15 schema fixtures parse to expected `Schema` (golden JSON snapshots in `tests/unit/parser/golden/`); every rejected construct has a test asserting the diagnostic code.

### Phase 2: Semantic Analysis

Build `SchemaGraph`: a `networkx.MultiDiGraph`, nodes = tables, edges = FKs (child → parent) with edge data = the `ForeignKey` + inferred cardinality.

**Cardinality inference:**
- FK columns are also PK or UNIQUE in child → **1:1**.
- Otherwise → **1:N** (parent 1, child N).
- **Junction table detection → M:N:** table has exactly 2 FKs to 2 different tables, its PK (or a UNIQUE) equals the union of the FK columns, and it has ≤ 2 non-key "payload" columns. Mark as `JunctionTable(left, right, payload_columns)`.
- Self-referencing FK → `SelfReference` (tree/hierarchy).

**Validators (each its own function, each its own diagnostic code):**

| Code | Severity | Check |
|---|---|---|
| `SEM001` | error | FK references a table that doesn't exist |
| `SEM002` | error | FK references columns that don't exist |
| `SEM003` | error | FK column count ≠ referenced column count |
| `SEM004` | error | FK type mismatch (after normalization; allow INTEGER→BIGINT widening as warning) |
| `SEM005` | error | FK references columns that are not PK/UNIQUE in parent |
| `SEM006` | error | Duplicate table / duplicate column |
| `SEM007` | error | PK/UNIQUE/INDEX references unknown column |
| `SEM008` | warning | Table has no primary key |
| `SEM009` | warning | Circular FK chain (report the cycle path) |
| `SEM010` | warning | `ON DELETE SET NULL` on a NOT NULL column |
| `SEM011` | info | Self-referencing FK detected |
| `SEM012` | error | Query references unknown table/column |

**Acceptance:** each code has positive + negative tests; cycle detection tested on 2-, 3-, and self-cycles.

### Phase 3: IR

The IR is a flat list of **guarantee nodes** plus **entity nodes**, wrapped in `IRProgram`. Each node has a stable `id` (e.g., `fk:orders.customer_id->customers.id`), a `source_span`, and `origin` (which SQL construct produced it).

**Entity nodes:** `EntityNode(table)`, `AttributeNode(table, column, type)`, `RelationshipNode(parent, child, cardinality, fk_ref)`.

**Guarantee nodes (minimum set):**

| IR Node | Produced by | Meaning |
|---|---|---|
| `ReferentialIntegrity(child, parent, cols)` | FK (any) | child row can't reference a non-existent parent |
| `CascadingDelete(parent, child)` | `ON DELETE CASCADE` | deleting parent deletes children |
| `CascadingUpdate(parent, child)` | `ON UPDATE CASCADE` | updating parent key updates children |
| `SetNullOnDelete` / `SetDefaultOnDelete` | `ON DELETE SET NULL/DEFAULT` | child FK nulled/defaulted |
| `RestrictDelete(parent, child)` | `RESTRICT` / `NO ACTION` | can't delete parent with children |
| `EntityUniqueness(table, cols)` | PK | rows are uniquely identifiable |
| `ValueUniqueness(table, cols)` | UNIQUE / unique index | no duplicate values |
| `CrossEntityUniqueness(tables, cols)` | unique across a junction/derived shape (detected in Phase 3 when a uniqueness guarantee spans embedded/related entities) | uniqueness spanning entities |
| `NotNullGuarantee(table, col)` | NOT NULL | value always present |
| `DomainConstraint(table, expr)` | CHECK | value satisfies predicate |
| `TypeGuarantee(table, col, type)` | column type | values have exact type/precision |
| `DefaultValue(table, col, expr)` | DEFAULT | server fills missing value |
| `AutoIncrement(table, col)` | SERIAL / IDENTITY | server-generated monotonic ID |
| `EnumDomain(table, col, values)` | ENUM type | value in fixed set |
| `MultiEntityAtomicity(tables, txn_id)` | `BEGIN...COMMIT` touching ≥ 2 tables | all-or-nothing across tables |
| `JoinSemantics(query_id, kind, tables)` | SELECT with JOIN | join result semantics (NULL handling for LEFT) |
| `AggregateSemantics(query_id, ...)` | GROUP BY / aggregates | grouping semantics |

`printer.py` renders the IR as readable text; the UI shows it in an "IR" tab. Document every node in `docs/IR.md`.

**Acceptance:** builder tests for every SQL construct → expected node(s); property test (hypothesis): randomly generated valid schemas never crash the builder, and every FK yields exactly one `ReferentialIntegrity` node.

### Phase 4: Equivalence Checker (the graded core, spend the most effort here)

**Architecture:**

```python
@rule(node_type=CascadingDelete, rule_id="EQ-CASCADE-DEL")
def check_cascading_delete(node: CascadingDelete, ctx: RuleContext) -> Verdict: ...
```

- `RuleContext` contains: the `SchemaGraph`, the **placement plan** (embed/reference per relationship; see note below), target Mongo version, and options (e.g., `transactions_available: bool`).
- `Verdict = { node_id, rule_id, status: SAFE|CHANGED|BROKEN, reason: str, mitigation: str | None, mongo_feature: str | None, conditions_evaluated: list[ConditionTrace] }`.
- `ConditionTrace` records each condition the rule checked and its result. The UI shows this as an expandable "Why?" so the reasoning is transparent (this is what proves it's not a flat switch).
- Reason and mitigation strings come from templates in the registry, filled with node data.

**Important ordering note:** many verdicts depend on embed vs. reference. Pipeline order is: IR → *initial* equivalence pass (placement-independent rules) → Optimizer → *final* equivalence pass with the placement plan. Both passes are recorded; the report shows the final one and flags verdicts that changed because of placement.

**Rule table (implement all; each condition must be a tested branch):**

| Rule | Conditions → Verdict |
|---|---|
| `ReferentialIntegrity` | child embedded in parent → **SAFE** (structurally impossible to orphan). Referenced → **CHANGED**: Mongo has no FK enforcement; mitigation: validate in app layer / use transactions on insert. |
| `CascadingDelete` | child embedded → **SAFE** (deleting the document deletes the subdocument). Referenced → **BROKEN**: no native cascade; mitigation: app-level delete inside a multi-document transaction, or a change-stream worker. If the cascade chain is depth > 1 and any hop is referenced → **BROKEN** with the full chain in the reason. |
| `CascadingUpdate` | parent key is PK and generated codegen uses immutable `_id` → **CHANGED** (keys don't change in practice, but guarantee is not enforced). Mutable natural key + referenced → **BROKEN**. Embedded → **SAFE**. |
| `SetNullOnDelete` / `SetDefault` | referenced → **BROKEN** (mitigation: app logic). Embedded → **SAFE** (child goes away with parent; note semantic difference: child is deleted, not nulled → actually **CHANGED**, explain). |
| `RestrictDelete` | referenced → **CHANGED** (must check for children before delete in app/transaction). Embedded → **CHANGED** (deleting parent always removes children; restriction semantics lost). |
| `EntityUniqueness` | single-column PK → **SAFE** (`_id` or unique index). Composite PK → **SAFE** (compound `_id` subdocument or compound unique index; note which codegen chose). PK of an embedded child → **CHANGED** (unique indexes do not enforce uniqueness *within* one document's array). |
| `ValueUniqueness` | top-level collection field(s) → **SAFE** (unique / compound unique index). Nullable unique column → **CHANGED** (Postgres allows multiple NULLs; Mongo unique index treats missing/null as a value; mitigation: partial index with `partialFilterExpression: {col: {$exists: true}}`), so emit that partial index. Field inside an embedded array → **CHANGED** (not enforced within a single document). |
| `CrossEntityUniqueness` | spans ≥ 2 collections → **BROKEN**. Collapses into one collection due to embedding → re-evaluate as `ValueUniqueness`. |
| `NotNullGuarantee` | → **SAFE** via `$jsonSchema` `required` + `bsonType` excluding `"null"`. |
| `DomainConstraint` | translatable predicate (comparisons, `IN`, `BETWEEN`, `LENGTH`, `AND/OR/NOT`, single-row column refs) → **SAFE** via `$jsonSchema` / `$expr` validator. Cross-column within same row → **SAFE** via `$expr`. Uses functions/subqueries we can't translate → **CHANGED** with the untranslated fragment shown. On embedded child → apply on nested path (**SAFE** if translatable). |
| `TypeGuarantee` | map via type table (below). Exact map → **SAFE**. Lossy (e.g., `VARCHAR(n)` length → `maxLength` OK = SAFE; `TIMESTAMPTZ` → BSON Date stores UTC only, offset lost → **CHANGED**; `INTERVAL`, `TIME` → **CHANGED** (stored as string/number); `NUMERIC(p,s)` → Decimal128 **SAFE** but precision/scale not enforced → **CHANGED** if scale matters (scale > 0)). |
| `DefaultValue` | constant default → **CHANGED** (Mongo has no server-side defaults; codegen emits defaults in the insert helper / Mongoose schema). `now()`/`CURRENT_TIMESTAMP` → **CHANGED** (same). |
| `AutoIncrement` | → **CHANGED**: replaced with `ObjectId`, or with a counter collection + `findOneAndUpdate($inc)` if the user selects `preserve_integer_ids`. Note ordering/gap semantics differ. |
| `EnumDomain` | → **SAFE** via `$jsonSchema` `enum`. |
| `MultiEntityAtomicity` | all touched tables embed into one document → **SAFE** (single-document atomicity). Otherwise, `transactions_available` (replica set / Atlas) → **CHANGED** (multi-document transactions work but have perf costs, 60s default limit, and require replica set). Not available → **BROKEN**. |
| `JoinSemantics` | INNER join on embedded relationship → **SAFE** (`$unwind`). INNER on referenced → **SAFE** via `$lookup` + `$unwind`. LEFT join → `$lookup` + `$unwind` with `preserveNullAndEmptyArrays: true` → **SAFE**, but if the query projects NULLs from the right side, flag **CHANGED** (missing field vs null). |
| `AggregateSemantics` | `COUNT(*)`, `SUM`, `MIN`, `MAX` → **SAFE**. `AVG` on integers → **CHANGED** if Postgres returns NUMERIC and Mongo returns double (precision). `COUNT(col)` → must skip nulls; emit `$sum: {$cond: [{$ne: ["$col", null]}, 1, 0]}`, **SAFE**. `SUM` on empty set: Postgres → NULL, Mongo → 0 → **CHANGED**. |

**Type map (`codegen` and equivalence share one table):**

| Postgres | BSON | Verdict notes |
|---|---|---|
| SMALLINT, INTEGER | int | SAFE (add `minimum/maximum` for SMALLINT range) |
| BIGINT | long | SAFE |
| NUMERIC/DECIMAL(p,s) | decimal | p/s not enforced → CHANGED when s > 0 |
| REAL, DOUBLE | double | SAFE |
| BOOLEAN | bool | SAFE |
| TEXT | string | SAFE |
| VARCHAR(n)/CHAR(n) | string + maxLength | SAFE (CHAR padding semantics → CHANGED) |
| UUID | binData subtype 4 (or string, configurable) | SAFE |
| DATE | date (midnight UTC) | CHANGED (time component added) |
| TIMESTAMP | date | SAFE (ms precision; µs truncated → CHANGED if seed data has µs) |
| TIMESTAMPTZ | date | CHANGED (offset lost, stored UTC) |
| JSON/JSONB | object | SAFE |
| BYTEA | binData | SAFE |
| ENUM | string + enum | SAFE |
| arrays (`INT[]`) | array of mapped type | SAFE |
| INTERVAL, TIME | string | CHANGED |

**Also build:** `rules_doc.py` that generates `docs/RULES.md` straight from the registry (rule id, node type, every condition, every verdict, reason templates). CI fails if the doc is stale.

**Acceptance:**
- Every rule × every condition branch has a unit test (parametrize).
- The labeled corpus (Section 10) achieves **detection rate ≥ 95%** and **false positive rate ≤ 5%**; report numbers.
- Report summary: counts of SAFE/CHANGED/BROKEN, an overall verdict (`BROKEN` if any BROKEN, else `CHANGED` if any CHANGED, else `SAFE`).

### Phase 5: Optimizer (embed vs. reference)

**Features per relationship** (`features.py`):
- `cardinality` (1:1, 1:N, M:N, self)
- `child_independent_access`: fraction of supplied queries that read the child without the parent (default 0.3 if no queries given; user can override per relationship in the UI)
- `read_together_ratio`: fraction of queries that join parent+child
- `child_write_frequency`: from UPDATE/INSERT queries on the child (low/med/high; default med)
- `estimated_child_count_per_parent`: from seed data if provided, else user hint, else default (1:1 → 1, 1:N → 20)
- `estimated_child_doc_size_bytes`: sum of column type size estimates
- `child_has_other_parents`: child table has FKs to ≥ 2 parents
- `guarantees_needing_embedding`: count of BROKEN-if-referenced guarantees on this edge (cascade delete, multi-entity atomicity)

**Cost model** (`cost_model.py`): compute `embed_score` in [0, 1]; embed if ≥ 0.5. Weights live in a config file (`optimizer/weights.yaml`) so they're tunable and documented.

```
hard constraints (override score):
  - projected parent doc size (parent + children × size × safety factor 2) > 4 MB → REFERENCE
    (Mongo hard limit is 16 MB; we use 4 MB as a safe ceiling)
  - M:N → REFERENCE (or array of refs on the side with lower fan-out), never full embed
  - child_has_other_parents → REFERENCE (embed in at most one parent)
  - unbounded growth (child count per parent > 1000 estimated) → REFERENCE

soft score (weighted sum, normalized):
  + 0.30 × read_together_ratio
  + 0.20 × (1 if 1:1 else 0.6 if 1:N small (<= 50) else 0)
  + 0.20 × min(guarantees_needing_embedding, 2) / 2
  − 0.20 × child_independent_access
  − 0.10 × child_write_frequency_norm
```

**Planner** (`planner.py`): resolves the global plan. Rules: a table can be embedded in at most one parent; process from leaves upward; detect and break FK cycles by referencing the lowest-scoring edge; nested embedding depth ≤ 3. Output `PlacementPlan { relationship_id → EMBED | REFERENCE | REF_ARRAY, score, top_factors: list[(factor, contribution)] }`.

**Acceptance:** unit tests for each hard constraint, the score formula, cycle-breaking, and multi-parent conflict. Golden-plan tests for the 4 sample schemas.

### Phase 6: Code Generation

Generate from `Schema + PlacementPlan + EquivalenceReport`:

1. **Collections + validators:** `db.createCollection(name, { validator: { $jsonSchema: {...} }, validationLevel: "strict", validationAction: "error" })`. Embedded children become nested `object` / `array` properties with their own `required`, `bsonType`, and translated CHECK constraints.
2. **Indexes:** unique / compound unique / partial unique (for nullable uniques), and an index on every referencing field (FK equivalents).
3. **Query translation** (`codegen/queries.py`): SQL SELECT AST → aggregation pipeline. Map: `WHERE` → `$match` (push down before `$lookup` when the predicate touches only the base collection), `JOIN` → `$unwind` (embedded) or `$lookup` + `$unwind` (referenced), `GROUP BY` → `$group`, `HAVING` → `$match` after `$group`, `ORDER BY` → `$sort`, `LIMIT/OFFSET` → `$skip`/`$limit`, projection → `$project` with output field names matching SQL column aliases exactly (needed for diffing). `INSERT/UPDATE/DELETE` → `insertOne/updateMany/deleteMany` (with embedded paths using `$push`, `$set` with positional operators, `$pull`).
4. **App-level enforcement helpers** for every CHANGED/BROKEN verdict that has a mitigation: e.g., a generated `delete_customer_cascade(session, customer_id)` function that runs in a transaction. Clearly commented with the verdict id it mitigates.
5. **Data migration script:** reads from Postgres, builds nested documents per the plan, writes to Mongo in batches (1000), idempotent (upsert by `_id`), prints counts per collection.
6. **Emitters:** `mongosh` JavaScript (primary) and Python/pymongo. Design `emit.py` with an `Emitter` interface so Mongoose/TypeScript can be added later.

Every generated file starts with a header comment: generator version, run id, timestamp, and "Generated by SchemaShift. Do not edit manually."

**Acceptance:** snapshot tests of generated output for all samples; generated `mongosh` validators load without error against a real Mongo (integration test); every SQL query in the samples translates.

### Phase 7: Verification Harness

1. **Sandbox** (`sandbox.py`): per run, create a Postgres schema `run_<uuid>` using a **restricted role** (no superuser, `statement_timeout = 5s`, `search_path` locked to that schema), and a Mongo database `run_<uuid>`. Always drop both in a `finally` block; plus a janitor that drops sandboxes older than 1 hour.
2. **DDL execution:** re-emit DDL from the parsed `Schema` via sqlglot (never the raw user string).
3. **Seed data** (`seed.py`): if user provided INSERTs, use them (re-emitted from AST). Otherwise generate synthetic data with `Faker`, respecting types, NOT NULL, UNIQUE, CHECK (simple ranges), FKs (insert in topological order), with configurable rows per table (default 50, max 500). Seeded RNG for reproducibility (store the seed in the run).
4. **Migrate** the seed data to Mongo using the generated migration logic.
5. **Run** each SQL query on Postgres and its translated pipeline on Mongo.
6. **Normalize** (`normalize.py`): Decimal ↔ Decimal128 → `Decimal`; datetimes → UTC, truncated to ms; ObjectId/UUID mapping through the id map built during migration; missing field ≡ NULL (but record it, since it can be a CHANGED signal); ints vs floats compared with tolerance 1e-9 for AVG.
7. **Diff** (`diff.py`): if the SQL has `ORDER BY` → ordered comparison; else multiset comparison. Output per query: `MATCH | MISMATCH`, row counts each side, first 5 differing rows, and a hypothesis ("SUM on empty group: PG NULL vs Mongo 0") when the pattern matches a known CHANGED rule.
8. **Guarantee probes** (stretch, but do it if time allows): for each BROKEN verdict, run a concrete probe that demonstrates it: e.g., delete a parent on both sides and show Postgres cascaded while Mongo left orphans. This makes the demo extremely convincing.

**Acceptance:** integration tests with testcontainers (Postgres 16 + Mongo 7 replica set) for all 4 samples; result-set correctness ≥ 95% on the corpus queries, with every mismatch explained by an existing CHANGED verdict.

### Phase 8: AI Layer (advisory only)

- `LLMAdvisor` protocol: `suggest_placement(rel: RelationshipFeatures, schema_excerpt: str) -> AISuggestion { decision: EMBED|REFERENCE, confidence: float, justification: str }`.
- Prompt in `prompts.py`: give the model the two table definitions, cardinality, feature values, and ask for **strict JSON** matching the schema. Validate with pydantic; on invalid JSON retry once, then mark `ai_unavailable`.
- Timeout 15s per call, run calls concurrently (bounded, max 5), cache by hash of (prompt template version + inputs) in the app DB.
- The optimizer's decision is **final**. Store both; compute `agree: bool`. When they disagree, the UI shows both side by side with the optimizer's top factors and the AI's justification.
- If no `ANTHROPIC_API_KEY` is set, use `MockAdvisor` and show "AI advisor disabled" in the UI. The whole app must work without an API key.
- Never send seed data or user rows to the LLM, only schema structure.

**Acceptance:** tests use `MockAdvisor`; one optional live test marked `@pytest.mark.live` skipped by default.

### Phase 9: API

FastAPI, all routes under `/api/v1`, OpenAPI docs at `/api/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + DB/Mongo connectivity |
| GET | `/samples` | list sample schemas |
| GET | `/samples/{name}` | sample SQL + queries |
| POST | `/compile` | body: `{ schema_sql, queries_sql?, seed_sql?, options }` → full `CompileResult` (diagnostics, IR dump, verdicts, plan, AI suggestions, generated code). No verification. Fast (< 2s for typical schemas). |
| POST | `/runs` | same body + `verify: bool`; creates a run, returns `{ run_id }`; verification runs as a background task |
| GET | `/runs/{id}` | run status (`pending/running/done/failed`) + full result |
| GET | `/runs` | paginated history |
| GET | `/runs/{id}/export?format=zip` | zip of all generated code + report JSON + report Markdown |
| GET | `/metrics` | latest corpus evaluation numbers |

**Options:** `{ target_mongo_version: "7.0", transactions_available: true, preserve_integer_ids: false, uuid_as: "binary"|"string", rows_per_table: 50, relationship_overrides: {rel_id: "EMBED"|"REFERENCE"}, access_hints: {rel_id: {...}} }`.

**Limits:** request body ≤ 200 KB; ≤ 50 tables; rate limit (e.g., `slowapi`) 30 compiles/min and 5 verified runs/min per IP; CORS restricted to the frontend origin via env.

### Phase 10: App Database

```
runs(id uuid pk, created_at, status, schema_sql text, queries_sql text, seed_sql text,
     options jsonb, overall_verdict, counts jsonb, seed int, duration_ms, error text, user_id fk null)
verdicts(id pk, run_id fk, node_id, rule_id, ir_node_type, status, reason, mitigation,
         conditions jsonb, source_span jsonb)
placements(id pk, run_id fk, relationship_id, decision, score, top_factors jsonb,
           ai_decision, ai_confidence, ai_justification, agree bool)
query_results(id pk, run_id fk, query_id, sql, pipeline jsonb, status, pg_rows int,
              mongo_rows int, diff jsonb)
ai_cache(key pk, response jsonb, created_at)
users(id pk, email, created_at)            -- Phase 13 expansion, create table now, unused
```

Alembic migrations for all of it. Index `runs.created_at`, `verdicts.run_id`.

### Phase 11: Frontend

**Home page layout** (desktop: two columns; mobile: stacked):
- **Left:** Monaco editor with tabs `Schema | Queries | Seed (optional)`, SQL syntax highlighting, "Load sample" dropdown, options drawer, buttons **Compile** (fast) and **Compile & Verify**.
- **Right:** result tabs:
  1. **Summary:** overall verdict banner, SAFE/CHANGED/BROKEN counts, diagnostics.
  2. **Verdicts:** sortable/filterable table (construct, IR node, status badge green/amber/red, reason, mitigation); clicking a row highlights the source SQL span in the editor via Monaco decorations; expandable "Why?" showing condition traces.
  3. **Relationships:** interactive graph (react-flow): tables as nodes, FKs as edges colored by EMBED/REFERENCE, edge click → placement panel (score, top factors, AI opinion, agree/disagree badge, override toggle that re-compiles).
  4. **IR:** printed IR, monospace.
  5. **Generated Code:** sub-tabs (Validators, Indexes, Queries, Migration, Enforcement Helpers), copy button, download zip.
  6. **Verification:** per-query MATCH/MISMATCH with expandable diff and hypothesis; live status polling while running.
- **Run detail page** `/runs/:id` is shareable and read-only.
- **History page:** list of past runs with verdict badges.
- **About page:** pipeline diagram and plain-English explanation (good for viva/demo).
- Dark mode, keyboard shortcut Ctrl/Cmd+Enter to compile, empty/loading/error states for every panel.
- Generate the TS API client from the backend OpenAPI schema; no hand-written fetch types.

### Phase 12: Metrics and Evaluation

`schemashift evaluate` CLI command + `/metrics` endpoint, computed over the labeled corpus:

| Metric | Definition |
|---|---|
| **Detection rate** (recall on problems) | TP / (TP + FN), where positives are IR nodes labeled CHANGED or BROKEN in ground truth; TP = predicted non-SAFE and correct severity |
| **False positive rate** | FP / (FP + TN), where FP = node labeled SAFE but predicted CHANGED/BROKEN |
| **Severity accuracy** | fraction of nodes whose predicted status exactly matches the label |
| **Result-set correctness** | MATCH queries / total verified queries |
| **AI agreement rate** | placements where AI == optimizer / placements with AI available |

Also output a confusion matrix (3×3). Write results to `docs/METRICS.md` with date and commit hash.

---

## 10. Test Corpus (build alongside Phases 1–4)

`backend/tests/corpus/` contains **≥ 25 labeled schemas**, each a folder with:
- `schema.sql`, optional `queries.sql`, `seed.sql`
- `expected.yaml`: expected diagnostics, expected verdict per IR node id (ground truth, hand-labeled with a one-line justification each), expected placements for key relationships

Coverage requirements:
- simple single tables; all column types in the type map
- 1:1, 1:N, M:N (junction), self-reference (employee → manager), multi-parent child
- every `ON DELETE` / `ON UPDATE` action
- single, composite, nullable UNIQUE; unique on embedded child
- CHECK: translatable (range, IN, cross-column) and untranslatable (function call)
- DEFAULT constants and `now()`; SERIAL; IDENTITY; ENUM
- multi-table transactions (embeddable and not)
- circular FKs; missing references (semantic errors)
- queries: joins, left joins with NULLs, group-by with empty groups, AVG precision
- 3 "real-world" style schemas (e-commerce, university, banking) with 8–12 tables each

---

## 11. Security (mandatory for public deployment)

- **Never run raw user SQL.** Only re-emitted SQL from the validated AST, and only statement kinds in a whitelist: `CREATE TABLE`, `CREATE INDEX`, `CREATE TYPE ... ENUM`, `ALTER TABLE ADD CONSTRAINT`, `INSERT`, `SELECT`, `UPDATE`, `DELETE` (the last two only inside the sandbox schema). Reject `COPY`, `DO`, `CREATE FUNCTION`, `GRANT`, `SET ROLE`, `pg_*` function calls, `dblink`, anything touching `information_schema`/`pg_catalog`.
- Sandbox Postgres role: `NOSUPERUSER NOCREATEDB NOCREATEROLE`, owns only its run schema, `statement_timeout=5s`, `idle_in_transaction_session_timeout=10s`, connection pool cap.
- Sandbox Mongo user: `readWrite` on `run_*` databases only. Use a separate Mongo user for the app.
- Size limits (body size, tables, rows, query count ≤ 30).
- Secrets only via environment variables; `.env` git-ignored; `.env.example` documents every variable.
- Rate limiting, CORS allowlist, security headers.
- LLM receives schema structure only, never seed data. Tell users in the UI not to paste confidential schemas.

---

## 12. Deployment

### Local (Phase 0 must make this work immediately)

`docker compose up` brings up:
- `postgres:16` (app DB + sandbox DB, separate databases)
- `mongo:7` started with `--replSet rs0` + an init container that runs `rs.initiate()` (transactions need a replica set)
- `backend` (Uvicorn, hot reload in dev)
- `frontend` (Vite dev server)

`make` targets (or `justfile`): `dev`, `test`, `test-integration`, `lint`, `typecheck`, `evaluate`, `migrate`, `seed-samples`, `build`.

### Production (target: free/cheap tiers)

| Component | Host |
|---|---|
| Frontend | Vercel (static Vite build, env `VITE_API_URL`) |
| Backend | Render, Railway, or Fly.io (Docker image, multi-stage build, non-root user, `uvicorn --workers 2`) |
| App + sandbox Postgres | Neon or Supabase (separate databases/roles for app vs. sandbox) |
| MongoDB | MongoDB Atlas (free M0 is a replica set, so transactions work) |
| Secrets | host's env var settings |

Write `docs/DEPLOYMENT.md` with step-by-step instructions for each host, every env var, and how to run Alembic migrations on deploy.

Backend must provide: `/api/v1/health` for host health checks, structured JSON logs (`structlog`) with run_id correlation, graceful shutdown that cleans up sandboxes, and Sentry hook (optional, env-gated).

### CI (`.github/workflows/ci.yml`)

On every push/PR: ruff, mypy, pytest unit (with coverage gate 85% on core packages), pytest integration (services: postgres, mongo replica set), stale `RULES.md` check, frontend lint + typecheck + vitest + build. On `main`: build and push Docker image; optional deploy hook.

---

## 13. Expansion Roadmap (design for these now; build after v1)

Keep these extension points clean so they're easy later:

1. **More source dialects:** MySQL, SQLite, SQL Server (sqlglot already supports them; make dialect a parameter everywhere).
2. **More targets:** DynamoDB, Cassandra, Firestore. Make the equivalence rules target-scoped (`@rule(node_type=..., target="mongodb")`) so a new target is a new rule pack + new codegen.
3. **Emitters:** Mongoose (TypeScript), Prisma for MongoDB, Spring Data.
4. **Workload-aware optimization:** import `pg_stat_statements` output to drive access-pattern features.
5. **Schema diff / evolution:** compare two versions of a schema and report new risks.
6. **Live migration mode:** connect to a real Postgres (read-only credentials), introspect schema, run migration to Atlas.
7. **Auth + saved projects:** GitHub OAuth, user workspaces, shareable public links.
8. **CLI + GitHub Action:** `schemashift check schema.sql --fail-on broken` for CI pipelines.
9. **VS Code extension:** inline verdict squiggles on `.sql` files using the same engine via the API.
10. **Rule plugins:** load custom rule packs from a directory.

---

## 9. Phase Order and Acceptance Gates (summary)

| # | Phase | Gate to pass before moving on |
|---|---|---|
| 0 | Scaffold: repo, tooling, docker-compose, CI skeleton, CLAUDE.md, health endpoint, empty frontend shell | `docker compose up` works; CI green; `/api/v1/health` returns ok |
| 1 | Parser | Golden tests for ≥ 15 fixtures; rejected constructs diagnosed |
| 2 | Semantic analysis | All SEM codes tested; cardinality + junction detection tested |
| 3 | IR | Every construct → node tests; hypothesis property tests pass; IR.md written |
| 4 | Equivalence checker | All rule branches tested; corpus detection ≥ 95%, FPR ≤ 5%; RULES.md generated |
| 5 | Optimizer | Hard constraints, scoring, planner tests; golden plans for samples |
| 6 | Codegen | Snapshot tests; validators load in real Mongo; all sample queries translate |
| 7 | Verification | Integration tests green; result-set correctness ≥ 95% with explained mismatches |
| 8 | AI layer | Works with mock and real key; app works with no key |
| 9–10 | API + app DB | OpenAPI complete; Alembic migrations; background runs; export zip |
| 11 | Frontend | All 6 result tabs; source highlighting; history + shareable runs; Playwright E2E for compile & verify on a sample |
| 12 | Metrics | `schemashift evaluate` prints all 5 metrics + confusion matrix; METRICS.md updated |
| 13 | Deployment | Deployed URLs working; DEPLOYMENT.md complete; security checklist (Section 11) verified item by item |

---

## 14. Demo Requirements

Prepare `samples/banking/` as the class demo: 5 tables (`customers`, `accounts`, `transactions`, `cards`, `branches`) that produces, at minimum:
- **SAFE:** NOT NULL, ENUM account type, unique email, 1:1 customer→KYC embed
- **CHANGED:** SERIAL ids, `DEFAULT now()`, `TIMESTAMPTZ`, nullable UNIQUE on phone, multi-table transfer transaction with replica set available
- **BROKEN:** `ON DELETE CASCADE` from accounts→transactions where transactions are referenced (unbounded growth forces REFERENCE), and a uniqueness constraint that spans collections
- A guarantee probe that shows orphaned transactions in Mongo after deleting an account

Add a "Demo" button on the home page that loads this sample and runs Compile & Verify.

---

## 15. Definition of Done (v1)

- All phase gates passed, CI green on `main`.
- Deployed frontend + backend reachable publicly; health check green.
- Corpus metrics computed and documented.
- README with: one-paragraph pitch, architecture diagram, screenshot/GIF, quickstart (`docker compose up`), link to live demo, link to docs.
- `docs/` complete: ARCHITECTURE, IR, RULES (generated), SUPPORTED_SQL, DECISIONS, METRICS, DEPLOYMENT.

**Start now with Phase 0. Before writing code, reply with: (1) your understanding of the project in 5 bullet points, (2) any spec issues or ambiguities you see, (3) your Phase 0 file plan. Then wait for my go-ahead.**
