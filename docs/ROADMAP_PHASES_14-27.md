# SchemaShift — Phases 14–27: Correctness, Intelligence, Production Readiness, Research

You are working in the SchemaShift repo (github.com/aryannn229/SchemaShift), a SQL → MongoDB
migration compiler. Phases 0–13 are complete and committed. This prompt adds Phases 14–27, grouped
into four stages that must be done in order:

- **Stage A — Correctness foundation** (Phases 14–17)
- **Stage B — Intelligent optimization** (Phases 18–20)
- **Stage C — Production-grade migration** (Phases 21–25)
- **Stage D — Research contribution** (Phases 26–27)

The theme is NOT "add more features". It is: make the compiler more correct, more intelligent,
and more convincing to demonstrate, with evidence for every claim.

---

## 0. Before you write any code

1. Read SPEC.md, CLAUDE.md, docs/DECISIONS.md, RULES.md and README.
2. Skim every package in backend/schemashift/ (models, parser, semantic, ir, equivalence,
   optimizer, codegen, verification, ai, metrics, api) and frontend/src.
3. Write a short "current state" note for me: how the rule registry works, how the optimizer cost
   model and YAML weights work, how the verification harness and result normalizer work, how
   runs are executed, and anything in this prompt that conflicts with the actual code.
4. Wait for my "go" before starting Phase 14.

## 1. Ground rules (apply to every phase)

**Process**
- Work ONE phase at a time. At the end of each phase, stop and report: summary of changes,
  files touched, new/changed rules, test results, coverage per core package, mypy/ruff status,
  new DECISIONS.md entries, known limitations, and open questions. Wait for my "go".
- If a phase is too large, propose splitting it before starting rather than delivering half of it.
- If anything in this prompt conflicts with how the code actually works, tell me instead of
  forcing it. Do not rewrite working code; extend it.

**Architecture**
- Compiler stages (models, parser, semantic, ir, equivalence, optimizer, codegen, and any new pure
  analysis packages) stay PURE. Only verification/, ai/, api/, and new I/O packages explicitly
  named below (fuzz/, bench/, migrate/runtime) may do I/O.
- All models are frozen pydantic v2 models. New IR nodes, verdicts and plans follow existing patterns.
- Rules are data, registered via the existing `@rule` registry. No if/else sprawl. Regenerate
  RULES.md from the registry after any rule change.
- The AI advisor stays advisory only. Deterministic rules and the optimizer remain authoritative.
  AI may suggest hypotheses or explain decisions; it never certifies correctness. Only schema
  structure is ever sent to the LLM, never data.
- Raw user SQL is never executed. Any SQL that runs (including fuzz-generated SQL) is re-emitted
  from the parsed AST and passes the statement whitelist.

**Quality**
- Tests are mandatory: pytest + hypothesis, ≥85% coverage on core packages, ruff clean,
  mypy --strict clean, TypeScript strict on the frontend. Use testcontainers for anything touching
  Postgres or Mongo. Frontend changes get Vitest tests; user-visible flows get a Playwright test.
- Log every non-trivial design decision in docs/DECISIONS.md (context, options, choice, why).
- Conventional Commits, small focused commits. Add a SPEC.md section for every new phase.

**Honesty**
- Never invent results or metrics. Every number reported must come from an actual run, with the
  command, seed and dataset recorded.
- Report correctness and performance SEPARATELY. A fast migration that silently changes behavior
  is still a bad migration.
- Embedding is not universally faster; never hard-code that assumption.
- Never claim automatic rollback for a lossy transformation that cannot be reversed.
- Solver-backed (Z3) reasoning applies only where constraints translate faithfully into a
  supported logical fragment. Everything else is reported as "unverified", never as SAFE.

---

# STAGE A — CORRECTNESS FOUNDATION

## Phase 14 — Fix known semantic mismatches + differential & metamorphic testing

Goal: discover semantic incompatibilities automatically instead of relying on 21 hand-written
queries.

1. **Fuzz package.** Create `backend/schemashift/fuzz/` (I/O allowed). Using hypothesis
   strategies, generate:
   - random schemas within the supported DDL subset: nullable and non-null columns, INTEGER,
     BIGINT, NUMERIC(p,s), TEXT/VARCHAR, BOOLEAN, DATE, timestamptz, enums, UNIQUE, CHECK,
     DEFAULT, serial/identity columns, single and composite keys;
   - unusual relationship structures: self-references, FK cycles, multi-parent children,
     junction tables, chains of 1:N, optional (nullable) FKs;
   - seed data that respects constraints and deliberately includes NULLs, empty strings,
     duplicates in non-unique columns, boundary numbers (0, negatives, max precision), and
     empty tables;
   - random SELECTs within the supported query subset: filters with AND/OR/NOT, comparisons
     including `<>`, `IN`, `BETWEEN`, `IS [NOT] NULL`, joins, GROUP BY, aggregates, HAVING,
     ORDER BY (including NULL ordering), LIMIT/OFFSET.
2. **Differential testing.** Run each case through the existing harness: Postgres original vs
   Mongo translated, then compare normalized results. Reuse the existing normalizer; extend it only
   if needed, and document every normalization (normalizations can hide real bugs).
3. **Metamorphic testing.** Add relations that must hold regardless of the expected output, e.g.:
   - `WHERE p` rows + `WHERE NOT p` rows + `WHERE p IS NULL` rows = all rows (TLP-style partitioning);
   - adding a tautological predicate must not change results;
   - reordering commutative AND/OR terms must not change results;
   - `COUNT(*)` must equal the number of rows the un-aggregated query returns;
   - translating the same query under EMBED vs REFERENCE plans must give identical results.
4. **Shrinking and regression corpus.** On a mismatch, let hypothesis shrink to a minimal repro and
   save it to `tests/regressions/fuzz_<hash>.yaml` (schema, seed, query, plan, both results, and
   the violated relation). Add a pytest that replays every saved regression forever.
5. **Fix the semantic bugs found in codegen**, explicitly targeting:
   - SQL three-valued logic vs Mongo null/missing (`x <> 5`, `NOT (x = 5)`, `x IN (...)` with
     NULLs, `NOT IN` with a NULL in the list);
   - NUMERIC ↔ Decimal128 precision, integer division, and rounding;
   - NULL ordering in ORDER BY (Postgres: NULLS LAST for ASC by default);
   - string comparison and collation; timestamptz round-tripping;
   - **the known gap:** global aggregates over empty input (Postgres returns 1 row with COUNT=0
     and other aggregates NULL; Mongo returns 0 rows). Fix it in codegen (e.g. a `$facet` or
     `$unionWith` fallback) and mark the gap closed in SPEC.md.
6. **CLI and CI.** Add `schemashift fuzz --cases N --seed S [--metamorphic]`. CI runs a small
   fixed-seed budget; a nightly GitHub Actions workflow runs a larger one and uploads new repros
   as artifacts.

**Acceptance:** fuzzer runs green at N=500 with a fixed seed after fixes; every bug found has a
saved regression and a DECISIONS.md entry describing the SQL vs Mongo semantic difference and
the fix.

---

## Phase 15 — Independent benchmark corpus & honest metrics

Goal: make the "100% detection, 0% false positives" claim defensible by separating self-labeled
data from independent data.

1. Add `samples/external/` with Postgres DDL (ported where needed) of public schemas: Pagila,
   Chinook, Northwind, and at least 2 schemas from permissively licensed open-source apps.
   Record the source URL, license and porting notes in a README for each schema. Schemas with
   unsupported constructs stay in the corpus; their diagnostics are data too.
2. Each external schema gets a realistic query workload and seed data (generated, or a small
   public dataset where licensing allows).
3. Define a YAML label format for expected verdicts per guarantee, with `labeled_by` and
   `labeled_blind: true|false` fields. Add `schemashift corpus label-template <schema>`, which
   emits an empty label file so a second person can label without seeing SchemaShift's output.
4. Extend metrics/ to report precision, recall, F1 and a SAFE/CHANGED/BROKEN confusion matrix
   **separately** for: (a) the self-labeled corpus and (b) the independently labeled corpus.
   Never merge them into one headline number.
5. **AI advisor agreement:** for every relationship in the corpus, record the optimizer decision
   vs the advisor recommendation (mock always; real when an API key exists). Report the raw
   agreement rate and Cohen's kappa, plus a table of disagreements showing both rationales.
6. **Mutation testing** with `mutmut`, scoped to equivalence/ and optimizer/. Report the mutation
   score and add tests that kill surviving mutants where reasonable.
7. Document failure cases explicitly: every false positive and false negative goes into
   docs/METRICS.md with an explanation.
8. Expose the new metrics through `/api/v1/metrics` and the About page.

**Acceptance:** `schemashift corpus report` prints both metric sets, kappa, mutation score and the
failure-case list; docs/METRICS.md explains methodology and limitations honestly.

---

## Phase 16 — Explainable verification engine: evidence levels & minimal counterexamples

Goal: turn the equivalence checker from a rules-based report into an explainable verification
engine.

1. Add an **evidence level** to every verdict, alongside SAFE/CHANGED/BROKEN:
   - `PROVEN` — established by a sound argument (structural rule now, solver in Phase 26);
   - `CONDITIONAL` — safe only under stated conditions (e.g. "safe only if the app always writes
     through the generated helper", or "safe only while the embedded array stays under N items");
   - `TESTED` — supported by fuzz/probe evidence but not proven;
   - `UNVERIFIED` — no evidence either way.
   Store the conditions as structured data, not just text.
2. **Minimal counterexample datasets.** For every BROKEN and CHANGED verdict where it is practical,
   generate the smallest seed data plus operation sequence that demonstrates the difference
   (reuse Phase 14 shrinking). Store it with the verdict.
3. **Side-by-side behavior.** Extend guarantee probes to run the counterexample on both databases
   and capture SQL behavior vs Mongo behavior (e.g. "Postgres: DELETE rejected by FK / cascaded
   3 rows; Mongo: parent deleted, 3 orphaned order_items").
4. The "Why?" trace must name the exact condition that caused the verdict (which rule, which
   inputs, which plan decision).
5. **Frontend:** in the Verdicts tab, show the evidence level badge, conditions, the counterexample,
   and the side-by-side outcome, with a button to replay the probe.

**Acceptance:** every BROKEN verdict on the sample corpus has either a reproducible minimal
counterexample or a documented reason why one could not be generated.

---

## Phase 17 — Transaction & concurrency guarantee analyzer

Goal: distinguish a guarantee that is *theoretically supported* by MongoDB from one that the
*generated implementation* actually preserves under concurrency.

1. Model the concurrency-relevant operations for each CHANGED/BROKEN guarantee that is enforced
   by app-level helpers. At minimum cover:
   - atomicity across multiple documents and collections (MultiEntityAtomicity);
   - concurrent inserts of duplicate values (with and without a unique index);
   - parent deletion racing with a child insert (referential integrity under REFERENCE);
   - concurrent updates to the same embedded array;
   - transaction write conflicts, `TransientTransactionError` retries and
     `UnknownTransactionCommitResult` handling.
2. Analyze statically which generated helpers rely on read-then-write patterns (check-then-act
   races) vs atomic primitives (unique indexes, single-document atomic updates, transactions).
   Encode the findings as rules in the registry, never ad-hoc code.
3. **Concurrency probes** in verification/: run the generated helpers from N parallel
   clients/threads against the replica set (testcontainers) for a fixed number of iterations,
   and report observed violations (duplicates, orphans, partial writes) with counts and seeds.
4. Fix codegen where possible: prefer unique indexes as enforcement, wrap multi-document writes
   in transactions with correct retry loops, and document guarantees that cannot be made safe.
5. Feed results into Phase 16 evidence levels (e.g. "SAFE in single-writer, BROKEN under
   concurrency without transaction").

**Acceptance:** for each concurrency scenario, a reproducible probe either shows zero violations
across the configured iterations (and says that is test evidence, not proof) or demonstrates
the violation with a seed.

---

# STAGE B — INTELLIGENT OPTIMIZATION

## Phase 18 — Workload-aware schema design

Goal: base embed/reference decisions on how the data is actually used, not only on cardinality.

1. Accept workload annotations: per-query frequency and read/write ratios, via a
   `-- @freq 1000` comment or a sidecar `workload.yaml`. Also allow write operations
   (INSERT/UPDATE/DELETE templates) in the workload so write amplification of embedding is
   modeled.
2. When seed data exists, compute statistics (average and max fan-out per relationship, value
   distributions, estimated document sizes) and use them instead of defaults. Keep all hard
   constraints (4 MB ceiling, M:N never embeds, multi-parent children, unbounded growth).
3. Extend the cost model with workload terms and document the formula in SPEC.md. Weights stay
   in YAML.
4. **Sensitivity analysis:** perturb each YAML weight by ±10% and ±20% and report which decisions
   flip. Surface "fragile decisions" in the Relationships tab.
5. Every decision gets an explanation listing the top cost contributors.

**Acceptance:** the same schema with different workloads produces different, explained plans;
the sensitivity report is reproducible.

---

## Phase 19 — Cost-based aggregation pipeline optimizer

Goal: optimize the generated pipelines, not just schema structure, while provably preserving
query semantics.

1. Add a pure pipeline-IR and a rewrite framework where each rewrite is a registered rule with
   explicit **semantic preconditions**. Implement at least:
   - push `$match` earlier (before `$lookup`/`$unwind`/`$group` only when the predicate
     references fields available at that point and the move does not change NULL/missing
     semantics);
   - remove unnecessary `$lookup` stages (e.g. the joined data is already embedded, or the joined
     fields are never used);
   - insert `$project` stages to drop unused fields early and shrink intermediate documents;
   - merge adjacent `$match`/`$project` stages; turn `$sort` + `$limit` into a top-k pattern;
   - choose index-friendly predicate shapes, and recommend or generate indexes that the
     pipeline can use.
2. Enumerate alternative pipelines (bounded search) and pick the cheapest by an estimated cost
   model (documents scanned, `$lookup` count, intermediate size, index use).
3. Validate estimates against reality: run `explain("executionStats")` on the candidates and
   report the correlation between predicted and observed cost (Spearman rho).
4. **Safety net:** every rewrite must pass the Phase 14 differential and metamorphic suites.
   Add a fuzz mode that compares the unoptimized vs optimized pipeline on Mongo directly.
5. Show the pipeline before and after optimization in the Generated Code tab, with the rewrites
   applied and the estimated vs observed cost.

**Acceptance:** optimized pipelines are result-identical to unoptimized ones across the fuzz
budget; the before/after cost report comes from real explain() runs.

---

## Phase 20 — Performance benchmarking laboratory

Goal: show with measurements whether the chosen design actually performs better.

1. Create `backend/schemashift/bench/` (I/O allowed), running from the CLI only, never on the
   public web app. It must:
   - generate datasets at multiple scales (e.g. 1k, 100k, 1M rows) from the schema plus workload;
   - run the equivalent workload on Postgres and on Mongo under: the optimizer's chosen plan,
     the all-REFERENCE plan, and the all-EMBED-where-legal plan;
   - record latency (p50/p95/p99), throughput, index usage, and storage size (data plus indexes)
     on each side;
   - use warm-up runs, multiple repetitions, fixed seeds, and record machine and version info.
2. Before any timing, verify that the results are correct (Phase 14 diff). Report correctness and
   performance in separate sections.
3. Output a JSON results file plus an HTML report with charts. Add a "Benchmarks" view on the
   frontend that renders uploaded results files.

**Acceptance:** `schemashift bench --scale 100k` produces a reproducible report showing whether
the chosen plan beat the alternatives, including the cases where it did not.

---

# STAGE C — PRODUCTION-GRADE MIGRATION

## Phase 21 — Production infrastructure & confirmed deployment

1. Replace in-memory rate limits with Redis-backed limits that work across workers; local dev
   must still work without Redis.
2. Replace the in-process thread pool with a real job queue (arq preferred; justify in
   DECISIONS.md if you choose otherwise). It needs retries, timeouts, cancellation, and run status
   persisted in Postgres.
3. Deploy to the target stack: Vercel (frontend), Render (API plus worker), Neon (Postgres), and
   Atlas (Mongo replica set). Write docs/DEPLOY.md with exact steps and env vars.
4. Add a post-deploy smoke test against the live URL and an uptime/status badge in the README.
   **Deployment is not done until the smoke test passes against the live URL.**
5. Security re-check: raw user SQL never executes, generated SQL passes the whitelist, the body
   limit, CORS allowlist and security headers are still in place, and the LLM receives schema
   structure only.

---

## Phase 22 — Incremental schema migration compiler

Goal: move from one-time conversion to ongoing schema evolution.

1. New input mode: two DDL versions (old, new), plus a supported subset of ALTER TABLE as input.
   Build a pure schema differ producing typed change ops: add, drop, rename and retype column;
   change primary key; add/drop FK; add/drop UNIQUE/CHECK/NOT NULL/DEFAULT; add/drop index;
   add/drop table. Detect likely renames heuristically, but require confirmation for them.
2. Re-plan with the optimizer and detect plan changes (e.g. a relationship becomes M:N and must
   switch from EMBED to REFERENCE, which forces document restructuring).
3. Generate an **ordered** migration plan: collMod validator updates, index create/drop,
   backfills, `$unset`s, document restructuring (embed ↔ reference transformations), with step
   dependencies.
4. Classify each step as reversible, reversible with saved data, or irreversible (lossy).
   Generate rollback steps only for the reversible ones; for the rest, generate a recovery
   strategy (e.g. snapshot first).
5. Produce a **guarantee diff**: which verdicts changed between v1 and v2, and why.
6. API: `POST /api/v1/diff`. Frontend: "Compare versions" mode with side-by-side editors, a plan
   view and the guarantee diff.

**Acceptance:** the harness applies the v1 migration, then the incremental v1→v2 migration, and the
resulting Mongo state equals a fresh v2 migration on the same seed data.

---

## Phase 23 — Safe migration mode

Goal: migrations a developer would trust on real data.

1. Add `migrate/runtime` (I/O allowed) that executes both full and incremental migrations with:
   - **dry run**, which reports what would change, record counts, estimated time and storage,
     without writing anything;
   - **pre-migration validation**, which checks source data against the target validators and
     reports the records that would fail, with reasons;
   - **checkpoints and resumability**, through batched processing with a persisted cursor so a
     crashed run resumes where it stopped and is idempotent on re-run;
   - a **migration manifest** recording completed steps, batch progress, failed records
     (quarantined, not dropped), and the recovery actions taken;
   - **post-migration consistency checks**: row/document counts, key coverage, FK coverage under
     REFERENCE, embedded array counts vs child rows, and sampled deep comparison.
2. Rollback only for steps classified reversible in Phase 22. For irreversible steps, require an
   explicit `--i-understand-this-is-lossy` flag and a recorded snapshot.
3. CLI: `schemashift migrate plan|dry-run|run|resume|verify|status`.

**Acceptance:** an integration test kills a migration mid-run, resumes it, and passes the
consistency checks with no duplicates or missing records.

---

## Phase 24 — Migration risk score & interactive decision simulator

1. **Risk score.** A transparent, rule-based report with categories: constraint loss, query
   incompatibility, migration complexity, runtime enforcement requirements, and concurrency risk.
   Each category's score is computed from explicit, testable rules in the registry and links to
   the verdicts or evidence behind it. No AI-generated numbers. Document the scoring formula in
   SPEC.md and test it.
2. **Decision simulator** (frontend plus a fast compile endpoint). On the Relationships graph, let the
   user flip any relationship between EMBED and REFERENCE, change workload frequencies, or toggle
   a constraint, and immediately see:
   - affected verdicts and evidence levels (diff vs the optimizer's plan),
   - generated indexes and validator changes,
   - pipeline changes and estimated cost,
   - the change in risk score.
   Hard-constraint violations (e.g. embedding M:N) are shown as blocked, with the reason.
   Add a "reset to optimizer plan" button. Overrides must never mutate the optimizer's own
   output; they produce a separate, labeled plan.

**Acceptance:** a Playwright test flips a relationship and asserts the verdicts, indexes and risk
score update.

---

## Phase 25 — Migration CI/CD integration & developer tooling

1. `schemashift check` CLI with `--baseline <old.sql>` and a policy file (`schemashift.policy.yaml`)
   that declares unacceptable regressions, e.g. "fail if any guarantee goes SAFE→BROKEN", "fail
   if a UNIQUE is removed", "fail if the risk score rises by more than X".
2. Output formats: human-readable text, JSON, and SARIF 2.1.0 with source spans.
3. A GitHub Action that runs on PRs touching .sql files. It uploads SARIF to code scanning, posts
   a summary comment naming the exact rule, table and verdict change (e.g. "UNIQUE removed on
   users.email → ValueUniqueness SAFE→BROKEN, rule EQ-UNIQ-003"), and fails the check per policy.
   Include a sample workflow in docs/.
4. Optional, separately packaged: a minimal LSP server (pygls) publishing SEM diagnostics and
   verdict hints on .sql files, plus a small VS Code extension client.

---

# STAGE D — RESEARCH CONTRIBUTION

## Phase 26 — SMT-backed constraint reasoning (Z3)

Goal: prove, where faithful, that generated validators accept exactly what the SQL constraints
accept, and produce counterexamples where they do not.

1. Add `z3-solver`. Create a pure module `equivalence/smt/` with a **documented supported
   fragment**: comparisons, AND/OR/NOT, IN lists, BETWEEN, integer and real arithmetic, string
   length, IS NULL, enum domains, NOT NULL, type ranges. Model NULL explicitly (SQL three-valued
   logic, where CHECK passes on UNKNOWN), plus Mongo missing vs null.
2. Encode the SQL side (DomainConstraint / CHECK / NOT NULL / EnumDomain / TypeGuarantee) and
   the Mongo side (the generated `$jsonSchema` fragment). Ask Z3 for a value accepted by one side
   and rejected by the other.
   - SAT → the verdict becomes CHANGED/BROKEN per existing rule semantics, and the counterexample
     is attached as Phase 16 evidence.
   - UNSAT → the evidence level is `PROVEN`.
   - Unsupported construct or timeout (configurable, default 2s) → `UNVERIFIED`, never SAFE.
3. Optional extension: use the solver to check the Phase 19 `$match` pushdown preconditions for
   predicates inside the fragment.
4. **Soundness test:** a hypothesis test checks that every Z3 counterexample really is
   accepted/rejected as claimed by real Postgres and real Mongo (testcontainers).
5. Frontend: "Proven" badge and counterexample display in the Verdicts tab.

**Acceptance:** zero counterexamples that fail real-database confirmation; the supported fragment
is documented in SPEC.md along with what is explicitly out of scope.

---

## Phase 27 — Reproducible evaluation & write-up

1. One command, `schemashift evaluate --all`, reproduces every reported number: corpus metrics
   (self vs independent), kappa, mutation score, fuzz budget and bugs found, concurrency probe
   results, optimizer and pipeline cost correlations, benchmark results, and the proven/
   conditional/tested/unverified breakdown. Record seeds, versions and hardware.
2. docs/EVALUATION.md: methodology, results tables, documented failure cases, threats to
   validity (author-written rules, corpus size, synthetic workloads), and the limits of Z3 and
   the benchmarks.
3. Update the README: what's new, honest headline metrics, known limitations, and an updated
   architecture diagram covering the new packages (fuzz, bench, migrate, smt, pipeline optimizer).
4. Update the About page to match.

---

## Start
Do Section 0 (read the repo and report its current state), then wait for my "go" to begin Phase 14.
