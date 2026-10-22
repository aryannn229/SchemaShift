# Phase 20: performance benchmarking laboratory

## What changed
- `schemashift/bench/` (I/O, CLI only, never imported by the API): `schemashift bench schema.sql -q queries.sql --scale 100k --reps 10 --out dir`
  generates seed data at the scale (rows per table), loads PostgreSQL and MongoDB under three plans (the optimizer's, all-REFERENCE, all-EMBED where legal),
  checks correctness first with the verifier's diff, then times only the queries that match: warm-up runs, repetitions, p50/p95/p99, throughput, index use
  (MongoDB `IXSCAN`, PostgreSQL `Index` nodes), documents examined, storage (data + indexes) and load time.
- Output: `results.json` and a self-contained `report.html` with inline SVG charts, machine, library and database versions, seed and parameters.
- Frontend: the Benchmarks page renders an uploaded results file (correctness, p50 and throughput bars, storage, findings, environment).
- `generate_seed(..., cap=...)` lifts the 500-row limit for the lab only. The unit test of the percentile function found an off-by-one (nearest-rank) that was fixed.

## Results (commands run 2026-10-23, laptop, both databases in Docker on the same host)
Recorded in `docs/bench/` (`ecommerce-1k`, `blog-100k`; open `report.html`).
- ecommerce, 1,000 rows per table: MongoDB p50 chosen / all-REFERENCE / all-EMBED: q1 24.5 / 23.9 / **4.5** ms, q2 6.8 / 25.9 / **5.5** ms, q3 **2.1** / 5.6 / 3.5 ms;
  PostgreSQL 1.0, 1.3, 0.4 ms. All plans returned correct results.
- blog, 100,000 rows per table: q1 162.9 / 823.6 / **152.4** ms, q2 452.6 / 2,628.4 / **421.0** ms, q3 **339.7** / 2,405.1 / 342.6 ms; PostgreSQL 0.33, 113.6, 82.0 ms.
- The chosen plan beat all-REFERENCE by a factor of 5 or more at 100k, was within 8% of all-EMBED, and was the fastest on 1 of 3 queries per sample.
  **PostgreSQL was faster than MongoDB on every query measured.**
- `pytest` unit: 1,251 passed. `pytest -m integration` (PostgreSQL 16 + MongoDB 7 replica set): 107 passed. ruff, `ruff format`, `mypy --strict` clean.
- Frontend: tsc, eslint, Vitest 60 tests, vite build; Playwright with the databases: 16 passed (4 visual specs skipped).

## Known limitations
- The 100,000-row run happened while other work ran on the same machine; treat it as direction, not as a figure to quote. 1,000,000 rows is supported but was not run
  (seed generation takes about 0.6 ms per row).
- Synthetic data and hand-written workloads; one machine; few repetitions.
