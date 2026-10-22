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

- A global aggregate (no GROUP BY) is wrapped in `$facet` with a default row, so an empty input still returns one
  row (`COUNT` 0, `SUM`/`AVG`/`MIN`/`MAX` NULL), as in PostgreSQL.
- `integer / integer` truncates toward zero (`$trunc($divide)`); any non-integer operand divides exactly.
- `ORDER BY` and `<` `<=` `>` `>=` on an **enum** column use the declaration order (`$indexOfArray`), as PostgreSQL
  does; `MIN`/`MAX` over an enum is reported as untranslatable. A repeated ORDER BY key keeps its first direction.
- `LIMIT 0` becomes `{$match: {$expr: false}}` (MongoDB rejects `$limit: 0`).
- MongoDB equality matches `null` with a missing field while SQL `NULL = x` is never true: a `$lookup` whose local key
  can be NULL (nullable column, or a row absent after a LEFT JOIN) is guarded.
- `(p) IS [NOT] NULL` over a boolean expression (the "unknown" truth value) is translated.

## String collation
Collections are created with the ICU `en` collation (`CompileOptions.string_collation = "en"`, the default), so
`ORDER BY` and `<`/`>` on text follow the case rules of an `en_US` PostgreSQL database (`a < A < b`). Use
`string_collation = "binary"` when the PostgreSQL database uses the `C` collation (byte order). **Known limit:**
PostgreSQL's libc collation ignores punctuation and spaces at the first comparison level (`'a%'` sorts before `'A'`),
ICU does not, so strings that differ only in punctuation or whitespace can sort differently. Equality stays exact. The
reproducer lives in `backend/tests/regressions/known_limitations/` and a test fails when it stops reproducing.
Collation can only be set when a collection is created (`collMod` cannot change it).

**Known gaps** (reported, not hidden):
- Translated DML does **not** cascade: use the generated `delete_<table>()` / `update_<table>()` helpers.
- Inserts into embedded tables need the parent key in the statement.

## Verification
`backend/tests/integration/` runs the same SQL on PostgreSQL and the translated pipeline on MongoDB for four
placement layouts (all referenced, embedded, nested, folded junction) and compares the results. It also loads every
generated artifact into a real MongoDB (Python and mongosh) and runs the generated `migrate.py` as a program.
Run with `docker compose -f docker-compose.test.yml up -d --wait`, then `pytest -m integration`.
