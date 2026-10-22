# supabase_slack_clone

Open-source app schema (Supabase Slack clone): users, channels, messages, roles and permissions with enums, identity keys, a function, a trigger and row level security. References auth.users, a table outside this file.

- **Source:** https://github.com/supabase/supabase/blob/8b4be6cdfd4dda985b0d62cc0b82efc65a10c583/examples/slack-clone/nextjs-slack-clone/full-schema.sql
- **Upstream commit:** `8b4be6cdfd4dda985b0d62cc0b82efc65a10c583`
- **License:** Apache License 2.0 (https://github.com/supabase/supabase/blob/8b4be6cdfd4dda985b0d62cc0b82efc65a10c583/LICENSE)
- **Ported by:** `scripts/port_external.py` (mechanical, reproducible)

## Porting notes
Removed (no schema meaning): `SET`, `set_config()`, `ALTER ... OWNER TO`, `COMMENT ON`, `GRANT`/`REVOKE`,
`CREATE/ALTER EXTENSION`, `CREATE DATABASE`, psql meta-commands, `DROP ...`. Everything else is byte for byte as upstream: functions, triggers, views and
partitioning stay in on purpose, because SchemaShift's diagnostics for them are part of the benchmark.

## Workload
`queries.sql` is a hand-written workload of N queries over this schema (reads with joins and aggregates, plus a few
writes). `seed.sql` is intentionally absent: verification generates constraint-respecting synthetic data (seeded).

