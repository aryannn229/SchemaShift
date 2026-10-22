# northwind

Classic Microsoft Northwind trading company: 14 tables (orders, products, employees, ...).

- **Source:** https://github.com/pthom/northwind_psql/blob/cd0ef28d66369fbe177778e604e4be0f153c9e5c/northwind.sql
- **Upstream commit:** `cd0ef28d66369fbe177778e604e4be0f153c9e5c`
- **License:** Microsoft Public License (Ms-PL) (https://github.com/pthom/northwind_psql/blob/cd0ef28d66369fbe177778e604e4be0f153c9e5c/LICENSE)
- **Ported by:** `scripts/port_external.py` (mechanical, reproducible)

## Porting notes
Removed (no schema meaning): `SET`, `set_config()`, `ALTER ... OWNER TO`, `COMMENT ON`, `GRANT`/`REVOKE`,
`CREATE/ALTER EXTENSION`, `CREATE DATABASE`, psql meta-commands, `DROP ...`, and the data (`INSERT`/`COPY`). Everything else is byte for byte as upstream: functions, triggers, views and
partitioning stay in on purpose, because SchemaShift's diagnostics for them are part of the benchmark.

## Workload
`queries.sql` is a hand-written workload of N queries over this schema (reads with joins and aggregates, plus a few
writes). `seed.sql` is intentionally absent: verification generates constraint-respecting synthetic data (seeded).

