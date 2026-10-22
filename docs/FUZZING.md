# Differential and metamorphic fuzzing (Phase 14)

`schemashift fuzz` looks for places where the generated MongoDB code does not behave like the SQL it came from,
instead of relying on a few hand-written queries.

```
schemashift fuzz --cases 500 --seed 14 --metamorphic [--save-dir backend/tests/regressions]
```
Needs `SANDBOX_ADMIN_DATABASE_URL` and `MONGO_URL` (see `docker-compose.test.yml`). A run is reproducible from
`--seed`; the summary prints cases, queries compared, relation checks run/skipped and every untranslatable reason.

## How it works
1. **Generate** (`fuzz/strategies.py`, hypothesis): a schema (`fuzz/spec.py` models rendered to DDL), constraint-valid
   seed rows and 1-4 SELECTs. Cases are valid by construction; a case PostgreSQL rejects is skipped, not reported.
2. **Run** (`fuzz/harness.py`): compile, generated schema/indexes/migration, sandbox PostgreSQL + MongoDB (the Phase 7
   machinery). Every query is re-emitted from the parsed AST and passes the statement whitelist.
3. **Differential**: results compared with `verification/diff.py` (see Normalizations).
4. **Metamorphic** (`--metamorphic`, `fuzz/relations.py`):
   - *TLP*: `rows(p) + rows(NOT p) + rows((p) IS NULL) = rows(all)` on MongoDB;
   - *tautology*: AND-ing `(c IS NULL OR c IS NOT NULL)` changes nothing;
   - *commutativity*: reversing the terms of a top-level AND/OR changes nothing;
   - *count*: `COUNT(*)` equals the number of rows of the plain query;
   - *plans*: identical results under the default, all-REFERENCE and all-EMBED plans.
5. **Shrink and save**: hypothesis minimises the failing case; the last failing example is written to
   `tests/regressions/fuzz_<hash>.yaml` (schema, seed, queries, relation, observed values, status, fix).
   `tests/integration/test_fuzz.py` replays every saved regression forever.

## Normalizations (they can hide bugs, so they are all listed)
`verification/normalize.py`: Decimal128/Decimal/int/float become Decimal; datetimes become UTC truncated to
milliseconds; dates become midnight UTC; Binary subtype 4 becomes UUID; ObjectId becomes str; INTERVAL becomes
`PT<seconds>S`. `values_equal`: numbers are equal within a relative `1e-9`. `verification/diff.py`: ordered comparison
only when the SQL has ORDER BY (otherwise a multiset); the generator always orders by a total key. A missing field
equals NULL on the MongoDB side (by design: NULL is stored as an absent field). The metamorphic relations compare
multisets with `sort_key` (floats rounded to 6 significant digits).

## What is deliberately not generated
Strings with punctuation or whitespace (PostgreSQL's libc collation and ICU order them differently, see
docs/CODEGEN.md "String collation"); microsecond timestamps (BSON dates are millisecond); division by a column;
MIN/MAX over enums; bigint sums beyond 2^63. These are documented limits, not unknown bugs.

## Bugs found and fixed
Thirteen real defects, each with a regression file (DECISIONS.md has the semantic difference and the fix): `LIMIT 0`;
global aggregate over an empty input; repeated ORDER BY key; data loss when a nullable-FK child was embedded; string
collation; enum ordering (two shapes); INNER JOIN through a LEFT-joined embedded child; `$lookup` on possibly-absent
keys (two shapes); dropped FK column of a LEFT-joined embedded table; NULL flag of computed sort keys; integer division.
One limitation is kept as a documented reproducer (`tests/regressions/known_limitations/`).
