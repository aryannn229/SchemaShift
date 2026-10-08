# Supported SQL (PostgreSQL dialect)

The parser never crashes on input: each statement yields either its model or a `Diagnostic`
(with line/column), and parsing continues with the next statement.

## Supported

**DDL**
- `CREATE TABLE` with column constraints (`PRIMARY KEY`, `NOT NULL`/`NULL`, `UNIQUE`, `DEFAULT`,
  `REFERENCES ... ON DELETE/UPDATE ...`, `CHECK`) and table constraints (composite PK/FK/UNIQUE,
  named `CONSTRAINT x ...`, `DEFERRABLE`).
- `SERIAL`/`BIGSERIAL`/`SMALLSERIAL`, `GENERATED {ALWAYS|BY DEFAULT} AS IDENTITY`.
- `ALTER TABLE ... ADD [CONSTRAINT x] PRIMARY KEY | FOREIGN KEY | UNIQUE | CHECK` (several comma-separated actions allowed).
- `CREATE [UNIQUE] INDEX [name] ON t (cols) [WHERE predicate]`.
- `CREATE TYPE name AS ENUM (...)`.
- Schema-qualified names are reduced to the bare name (`public.users` -> `users`).
- Identifiers: unquoted -> lowercase, quoted -> preserved.

**Types:** SMALLINT, INTEGER, BIGINT, NUMERIC/DECIMAL(p,s), REAL, DOUBLE PRECISION, BOOLEAN, TEXT, VARCHAR(n),
CHAR(n), DATE, TIMESTAMP, TIMESTAMPTZ, TIME(TZ), UUID, JSON, JSONB, BYTEA, INTERVAL, user ENUMs, arrays of these.

**Queries**
- `SELECT` with `WHERE`, `INNER`/`LEFT JOIN ... ON a = b [AND ...]`, `GROUP BY`, `COUNT/SUM/AVG/MIN/MAX`,
  `HAVING`, `ORDER BY`, `LIMIT/OFFSET`, `DISTINCT`, `IN (list)`, `BETWEEN`, `LIKE`, `IS [NOT] NULL`.
- `INSERT ... VALUES`, `UPDATE ... WHERE`, `DELETE ... WHERE`.
- `BEGIN; ... COMMIT;` (also `START TRANSACTION`, `END`) -> `TransactionBlock`.

## Explicitly rejected (diagnostic code `UNSUPPORTED_<NAME>`)

| Code | Construct |
|---|---|
| `UNSUPPORTED_TRIGGER` | `CREATE TRIGGER` |
| `UNSUPPORTED_FUNCTION` | `CREATE FUNCTION` / `CREATE PROCEDURE` |
| `UNSUPPORTED_VIEW` | `CREATE [MATERIALIZED] VIEW` |
| `UNSUPPORTED_CTE` | `WITH` / `WITH RECURSIVE` |
| `UNSUPPORTED_WINDOW_FUNCTION` | `... OVER (...)` |
| `UNSUPPORTED_SUBQUERY` | any nested `SELECT` (correlated or not; see DECISIONS) |
| `UNSUPPORTED_OUTER_JOIN` | `RIGHT JOIN`, `FULL [OUTER] JOIN` |
| `UNSUPPORTED_JOIN` | cross join, `USING`, non-equality `ON` |
| `UNSUPPORTED_INHERITANCE` | `INHERITS` |
| `UNSUPPORTED_PARTITIONING` | `PARTITION BY` / `PARTITION OF` |
| `UNSUPPORTED_EXCLUSION_CONSTRAINT` | `EXCLUDE USING ...` |
| `UNSUPPORTED_LATERAL` | `LATERAL` |
| `UNSUPPORTED_CREATE_TABLE_AS` | `CREATE TABLE ... AS SELECT` |
| `UNSUPPORTED_EXPRESSION_INDEX` | indexes on expressions |
| `UNSUPPORTED_ALTER_ACTION` | any `ALTER TABLE` action except ADD constraint |
| `UNSUPPORTED_SET_OPERATION` | `UNION` / `INTERSECT` / `EXCEPT` |
| `UNSUPPORTED_FUNCTION_CALL` | functions other than COUNT/SUM/AVG/MIN/MAX/CAST in queries |
| `UNSUPPORTED_QUERY_FEATURE` | `DISTINCT ON`, `FOR UPDATE`, `RETURNING`, `ON CONFLICT`, `INSERT ... SELECT`, `UPDATE ... FROM`, `DELETE ... USING` |
| `UNSUPPORTED_ROLLBACK` | `ROLLBACK` |
| `UNSUPPORTED_STATEMENT` | `DO`, `COPY`, `GRANT`, `DROP`, other `CREATE`/`ALTER` objects, ... |

## Other parser diagnostics
`PARSE001` syntax error, `PARSE002` unrecognized statement, `PARSE003` statement ignored (wrong input tab),
`PARSE004` transaction structure, `PARSE005` multiple primary keys, `PARSE006` ALTER/INDEX on unknown table,
`PARSE007` unsupported column type, `PARSE008` unknown enum type, `PARSE009` duplicate type,
`PARSE010` ignored column constraint (warning), `SEM006` duplicate table (only the parser can see it).
