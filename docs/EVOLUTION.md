# Incremental schema migration (Phase 22)

`schemashift/evolve/` compares two versions of a schema and plans the MongoDB migration that takes existing data from the
first to the second. It is pure; the generated script is what touches a database.

```
old DDL ─┐                       ┌─> typed changes ───────────────┐
         ├─ differ (+ ALTER) ────┤                                ├─> ordered steps (+ rollback, reversibility)
new DDL ─┘   compile both ───────┴─> placement changes ───────────┘
                                 └─> guarantee diff (verdicts v1 vs v2)
```

## Input
Two DDL files, or the old DDL plus an `ALTER` script. Supported ALTER subset: `ADD COLUMN`, `DROP COLUMN`, `RENAME COLUMN`,
`RENAME TO`, `ALTER COLUMN TYPE / SET|DROP NOT NULL / SET|DROP DEFAULT`, `ADD CONSTRAINT` (PK, FK, UNIQUE, CHECK),
`DROP CONSTRAINT`, `CREATE TABLE`, `DROP TABLE`, `CREATE [UNIQUE] INDEX`, `DROP INDEX`. Anything else is reported in `warnings`
and skipped, never guessed.

## Typed changes
`ADD/DROP/RENAME_TABLE`, `ADD/DROP/RENAME/RETYPE_COLUMN`, `CHANGE_PRIMARY_KEY`, `ADD/DROP_FOREIGN_KEY`, `ADD/DROP_UNIQUE`,
`ADD/DROP_CHECK`, `SET/DROP_NOT_NULL`, `SET/DROP_DEFAULT`, `ADD/DROP_INDEX`.
**Renames are never assumed.** A dropped and an added column of the same type with a similar name is a *rename candidate*; it stays
a drop plus an add (which loses data) until the caller confirms it (`confirmed_renames`, `--confirm-rename`, the checkbox in the UI).
A `RENAME COLUMN` written in an ALTER script is explicit and needs no confirmation.

## Re-planning
Both versions are compiled with the optimizer. A relationship whose decision differs is a `PlanChange`; `restructures: true` means
documents must move (embedded <-> separate collection).

## Steps
Ordered, with `depends_on`, executable code (`apply`) and a rollback where one exists. Kinds: `CREATE_COLLECTION`,
`ARCHIVE_COLLECTION`, `UPDATE_VALIDATOR`, `CREATE_INDEX`, `DROP_INDEX`, `ADD_FIELD`, `UNSET_FIELD`, `RENAME_FIELD`, `RETYPE_FIELD`,
`EXTRACT_EMBEDDED`, `EMBED_COLLECTION`, `MANUAL`. Order: new collections, validators relaxed on collections being rewritten,
data steps, index drops, final validators (strict again), index creation.

| Reversibility | Meaning | Rollback |
|---|---|---|
| `REVERSIBLE` | information-preserving (add a defaulted field, rename a field/collection, widening numeric conversion, index/validator change) | generated |
| `REVERSIBLE_WITH_SAVED_DATA` | destroys data in place, so it is saved first (`_evolve_backup`, or the collection is renamed to `_archive_<name>`): drop a column, lossy conversion, move documents between collections | generated, restores the saved data |
| `IRREVERSIBLE` | cannot be automated or undone from SchemaShift (primary key change, `_id` column changes, a default that is not a literal, layout combinations without an automatic restructuring) | none; a `recovery` strategy (snapshot first) |

IRREVERSIBLE steps are `manual`: the generated `apply()` stops there and prints what to do. Nothing is ever deleted: dropped tables are
renamed to `_archive_<name>`.

Automatic restructuring covers a 1:N or 1:1 child embedded directly in a root document <-> its own collection (single-column keys,
the foreign key referencing the host `_id`). Deeper nesting, composite keys and folded junction tables are `MANUAL`.

## Guarantee diff
Verdicts of both versions are matched by their stable node id (`unique:users.email`; confirmed renames are mapped). A change is
`worse`, `better`, `added` or `removed`, with the rule id and the schema changes that explain it (`because`), for example
"UNIQUE removed on users.email: ValueUniqueness (EQ-VALUE-UNIQ) removed".

## Interfaces
`schemashift diff old.sql new.sql | --alter script.sql [--confirm-rename users.fullname=name] [--out dir]` writes `evolve.py` and
`plan.json`; `POST /api/v1/diff`; the **Compare versions** page.

## Verification (acceptance)
`tests/integration/test_evolve.py` on real PostgreSQL 16 and MongoDB 7: PostgreSQL executes the ALTER statements; MongoDB A is migrated
from v1 and evolved by the generated script; MongoDB B is migrated from scratch from the evolved PostgreSQL. A and B must hold
identical documents, indexes and validators, and `rollback()` must restore A to its v1 snapshot. Cases: field operations including
inside embedded arrays, new collection, defaults, retypes, index/validator changes, and embed <-> reference restructuring in both
directions.

## Limits
Assumes the tables are being altered under PostgreSQL's own rules (a NOT NULL column without default needs an empty table).
Backfills use literal defaults only. Text-to-number conversions keep values that do not parse (and the validator then flags them).
Concurrent writes during the migration are not handled: run it in a maintenance window or on a snapshot.
