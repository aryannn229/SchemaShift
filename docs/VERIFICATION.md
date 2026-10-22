# Verification harness

`schemashift.verification.verify(compile_result, options, SandboxConfig, seed_sql=..., seed=...)` proves the generated
MongoDB code against real databases. CLI: `schemashift verify schema.sql -q queries.sql` (needs
`SANDBOX_ADMIN_DATABASE_URL` and `MONGO_URL`).

## Steps
1. **Sandbox** (`sandbox.py`): per run a PostgreSQL role `run_<id>` (NOSUPERUSER, NOCREATEDB, NOCREATEROLE, connection limit 4,
   `statement_timeout` 5 s, `idle_in_transaction_session_timeout` 10 s, `search_path` locked) that owns only the schema
   `run_<id>`, and a MongoDB database `run_<id>`. Everything is dropped in `finally`; `sweep()` (the janitor) drops
   sandboxes older than one hour left behind by a crash.
2. **DDL** (`ddl.py`): re-emitted from the parsed `Schema` with every identifier quoted. Default expressions, CHECKs and index
   predicates are validated against a function whitelist (no `pg_*`, `dblink`, `set_config`, subqueries, qualified tables).
3. **Seed data** (`seed.py`): the user's `INSERT ... VALUES` statements (turned into bound parameters, never executed raw) or
   synthetic Faker data: reproducible from the seed, 50 rows per table by default (max 500), respecting types, NOT NULL,
   UNIQUE (including composite), simple CHECKs (ranges / IN lists are used to *generate* valid values, the rest is verified with
   an SQL evaluator), foreign keys in topological order (cycles are broken at nullable keys), 1:1 keys and junction pairs.
4. **Generated MongoDB code runs**: the generated `schema.py`, `indexes.py` and `migrate.py` are executed as emitted.
5. **Queries**: each SELECT runs on PostgreSQL (SQL re-emitted from the AST) and as the translated pipeline on MongoDB
   (`maxTimeMS` 5 s). `normalize.py` maps both sides to one representation (Decimal128 -> Decimal, datetimes in UTC at
   millisecond precision, UUID binary -> UUID, numeric tolerance 1e-9). `diff.py` compares as a multiset, or in order when
   the query has ORDER BY (rows tied on the sort key may permute), and lists the first five differing rows with a
   **hypothesis** (SUM over an empty set, numeric precision, global aggregate over no rows, NULL ordering, ...).
6. **DML**: executed on both sides; generated values (identity ids, `now()` defaults) are mirrored from PostgreSQL's
   `RETURNING` into the MongoDB document (the id map). When PostgreSQL rejects a statement, the MongoDB translation is tried on a
   snapshot: both rejecting is a MATCH, MongoDB accepting is a MISMATCH ("no native enforcement"). After a mismatch MongoDB is
   re-synchronised from PostgreSQL.
7. **Guarantee probes** (`probes.py`): for CHANGED/BROKEN verdicts (cascade, SET NULL/DEFAULT, RESTRICT, foreign-key integrity,
   key update) the same action is performed on both sides (PostgreSQL in a rolled-back transaction, MongoDB with a restored
   snapshot) and the resulting tables are compared, e.g. "PostgreSQL deleted the account and its 1 transaction, MongoDB left
   the orphan".

Every MISMATCH carries `explained_by`: the CHANGED/BROKEN verdict ids of the query's own IR nodes (joins, aggregates) or of the
foreign keys around the written table. `report.unexplained` must stay empty.

## Results (see `docs/METRICS.md`)
Result-set correctness over the corpus queries: 41 of 43 verified queries match (95.3%); the 2 mismatches are `INSERT`s whose
duplicate primary key is rejected by PostgreSQL but accepted when the table is embedded in an array
(rule `EQ-ENTITY-UNIQ/PK_EMBEDDED`, verdict CHANGED).

## Evidence levels, counterexamples and comparison allowances (Phases 15-16)

Every verdict carries an **evidence level** (`models/evidence.py`, table in `equivalence/evidence.py`, shown in
`docs/RULES.md`): `PROVEN` (a stated argument; platform facts such as "MongoDB has no cascade" or structural facts such as
"an embedded child cannot be orphaned"), `CONDITIONAL` (holds only under structured conditions, e.g. `app_enforcement`,
`validators_installed`, `replica_set`), `TESTED` (a verification run supports it) or `UNVERIFIED`. A verification run raises an
`UNVERIFIED` query-level verdict (join, aggregate) to `TESTED` when its query MATCHes (`VerificationReport.evidence_upgrades`,
with the seed and row count as the basis); PROVEN and CONDITIONAL verdicts keep their level, because a differential run is not a
proof. The "Why?" trace names the rule/outcome that fired (`decided_by`) and the inputs it consulted: the IR node, the placement
decision and score of its relationship, and the options it depends on.

**Minimal counterexamples** (`counterexample.py`): for each CHANGED/BROKEN verdict the harness builds the smallest dataset that
exercises it (one row per table that matters, the NOT NULL ancestors its foreign keys force, the cascade chain for a cascading
delete, two rows for a self-reference), loads it into a fresh sandbox, runs the operation on both databases and stores the setup
`INSERT`s, the operation, what each side did and the resulting difference. Verdicts that are properties of the representation
rather than of an operation (types, defaults, generated ids, uniqueness inside an array, ...) get a documented reason
(`GAP_REASONS`) instead; a test fails if any non-SAFE outcome has neither. Every BROKEN verdict of the four bundled samples has
a counterexample or a documented reason. `POST /api/v1/runs/{id}/counterexamples/replay?verdict_id=...` rebuilds and re-runs
one (same seed, same result); it shares the stricter verified-run rate limit.

**Comparison allowances** (each is recorded as a note on the query result, never silent): rows tied on the ORDER BY key at a
`LIMIT` cut-off may be different members (arbitrary in both databases); queries that reference a PostgreSQL `REAL` (float4)
column use a relative tolerance of 1e-5 because PostgreSQL computes in single precision and MongoDB in double; seed rows are
inserted parents-first whatever order the schema declares its tables.
