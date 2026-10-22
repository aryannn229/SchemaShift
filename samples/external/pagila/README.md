# pagila

DVD rental store (PostgreSQL port of MySQL Sakila): 22 tables, views, functions, triggers, a partitioned table. Deliberately keeps constructs SchemaShift rejects.

- **Source:** https://github.com/devrimgunduz/pagila/blob/ce6e114236bb828e5ec0dc835f65f1bf1b9179f5/pagila-schema.sql
- **Upstream commit:** `ce6e114236bb828e5ec0dc835f65f1bf1b9179f5`
- **License:** PostgreSQL License (https://github.com/devrimgunduz/pagila/blob/ce6e114236bb828e5ec0dc835f65f1bf1b9179f5/LICENSE.txt)
- **Ported by:** `scripts/port_external.py` (mechanical, reproducible)

## Porting notes
Removed (no schema meaning): `SET`, `set_config()`, `ALTER ... OWNER TO`, `COMMENT ON`, `GRANT`/`REVOKE`,
`CREATE/ALTER EXTENSION`, `CREATE DATABASE`, psql meta-commands, `DROP ...`. Everything else is byte for byte as upstream: functions, triggers, views and
partitioning stay in on purpose, because SchemaShift's diagnostics for them are part of the benchmark.

## Workload
`queries.sql` is a hand-written workload of N queries over this schema (reads with joins and aggregates, plus a few
writes). `seed.sql` is intentionally absent: verification generates constraint-respecting synthetic data (seeded).

