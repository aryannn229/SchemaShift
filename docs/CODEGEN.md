# Code generation

`generate_code(compile_result, options) -> CodegenResult` (pure, no database access) renders:

| File | Language | Content |
|---|---|---|
| `mongosh/01_collections.js`, `python/schema.py` | JS / Python | `createCollection` with `$jsonSchema` validators (idempotent: `collMod` when it exists) |
| `mongosh/02_indexes.js`, `python/indexes.py` | JS / Python | unique / compound / partial-unique indexes and a foreign-key-equivalent index per referencing field |
| `mongosh/03_queries.js`, `python/queries.py` | JS / Python | every SQL query as an aggregation pipeline or write operation; transactions in `withTransaction` |
| `python/migrate.py` | Python | PostgreSQL -> MongoDB data migration (batches of 1000, idempotent upserts by `_id`) |
| `python/helpers.py` | Python | application-level enforcement helpers for CHANGED / BROKEN verdicts |

Every file starts with the generator version, run id, timestamp and "Do not edit manually". The `Emitter`
protocol in `codegen/emit.py` is the extension point for other languages (Mongoose, TypeScript).
`schemashift compile schema.sql --queries q.sql --out build/` writes all files.

## Document layout (`codegen/layout.py`)
Every table is `root` (own collection), `object` (1:1 sub-document), `array` (1:N array of sub-documents) or a
folded junction (`ref_scalars`: array of ids, `ref_docs`: array of `{reference, payload}`).

- A single-column primary key is stored as `_id`; composite or missing keys use a generated ObjectId (the migration
  derives it deterministically so re-runs are idempotent) plus a compound unique index.
- Columns implied by nesting (the FK to the host) are not stored again; nested children keep their own keys.
- SQL NULL is represented by an **absent** field (validators exclude `null`; `$ifNull` re-creates NULL in results).

## Validators
Types follow the shared type map (`equivalence/typemap.py`). CHECK constraints become `$jsonSchema` keywords
(`minimum`, `enum`, `minLength`, `pattern`, ...) when they constrain one column against literals, otherwise a
NULL-tolerant `$expr` (a SQL CHECK passes on UNKNOWN). Checks on embedded arrays iterate with `$allElementsTrue/$map`.
Untranslatable checks are listed in `CodegenResult.untranslated_checks` and get a stub in `helpers.py`.

## Query translation (`codegen/queries.py`)
- WHERE becomes `$match`, pushed down before the first join when it only touches the base collection.
- Joins become `$unwind` (embedded) or `$lookup + $unwind` (referenced; the pipeline form is used when join columns are
  nullable so NULLs never match).
- GROUP BY becomes `$group`, HAVING a `$match`, ORDER BY a `$sort` with PostgreSQL NULL ordering emulated,
  LIMIT/OFFSET `$skip/$limit`. Projection names equal the SQL aliases.
- SUM over no non-null input yields NULL (as in PostgreSQL); COUNT(col) skips nulls.
- DML uses `insertMany`, `updateMany` (with `arrayFilters` for embedded rows) and `$pull`.
- SQL predicates use three-valued logic: NOT is pushed to the leaves and leaves are NULL-aware
  (`codegen/predicate.py`).

**Known gaps** (reported, not hidden):
- A global aggregate over an empty input returns no row in MongoDB (PostgreSQL returns one NULL/0 row).
- Translated DML does **not** cascade: use the generated `delete_<table>()` / `update_<table>()` helpers.
- Inserts into embedded tables need the parent key in the statement.

## Verification
`backend/tests/integration/` runs the same SQL on PostgreSQL and the translated pipeline on MongoDB for four
placement layouts (all referenced, embedded, nested, folded junction) and compares the results. It also loads every
generated artifact into a real MongoDB (Python and mongosh) and runs the generated `migrate.py` as a program.
Run with `docker compose -f docker-compose.test.yml up -d --wait`, then `pytest -m integration`.
