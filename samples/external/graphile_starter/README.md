# graphile_starter

Open-source app schema (Graphile Starter, first migration): users, emails, organizations, sessions, with many functions, triggers and policies. A stress test for unsupported constructs.

- **Source:** https://github.com/graphile/starter/blob/5f377879c06cf26b36946d23a31d885abc82dfc6/@app/db/migrations/committed/000001.sql
- **Upstream commit:** `5f377879c06cf26b36946d23a31d885abc82dfc6`
- **License:** MIT (https://github.com/graphile/starter/blob/5f377879c06cf26b36946d23a31d885abc82dfc6/GRAPHILE_STARTER_LICENSE.md)
- **Ported by:** `scripts/port_external.py` (mechanical, reproducible)

## Porting notes
Removed (no schema meaning): `SET`, `set_config()`, `ALTER ... OWNER TO`, `COMMENT ON`, `GRANT`/`REVOKE`,
`CREATE/ALTER EXTENSION`, `CREATE DATABASE`, psql meta-commands, `DROP ...`. Everything else is byte for byte as upstream: functions, triggers, views and
partitioning stay in on purpose, because SchemaShift's diagnostics for them are part of the benchmark.

## Workload
`queries.sql` is a hand-written workload of N queries over this schema (reads with joins and aggregates, plus a few
writes). `seed.sql` is intentionally absent: verification generates constraint-respecting synthetic data (seeded).

