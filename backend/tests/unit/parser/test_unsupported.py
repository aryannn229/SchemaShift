"""Every rejected construct yields a clear diagnostic (never a crash) and parsing continues."""

import pytest

from schemashift.parser import parse

CASES = [
    (
        "UNSUPPORTED_TRIGGER",
        "CREATE TRIGGER t BEFORE INSERT ON a FOR EACH ROW EXECUTE FUNCTION f();",
    ),
    (
        "UNSUPPORTED_FUNCTION",
        "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql;",
    ),
    (
        "UNSUPPORTED_FUNCTION",
        "CREATE OR REPLACE PROCEDURE p() LANGUAGE sql AS $$ SELECT 1 $$;",
    ),
    ("UNSUPPORTED_VIEW", "CREATE VIEW v AS SELECT 1;"),
    ("UNSUPPORTED_VIEW", "CREATE MATERIALIZED VIEW v AS SELECT 1;"),
    ("UNSUPPORTED_CTE", "WITH x AS (SELECT 1) SELECT * FROM x;"),
    ("UNSUPPORTED_CTE", "WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x;"),
    ("UNSUPPORTED_WINDOW_FUNCTION", "SELECT sum(a) OVER (PARTITION BY b) FROM t;"),
    ("UNSUPPORTED_SUBQUERY", "SELECT * FROM a WHERE EXISTS (SELECT 1 FROM b WHERE b.x = a.x);"),
    ("UNSUPPORTED_SUBQUERY", "SELECT * FROM a WHERE x IN (SELECT y FROM b);"),
    ("UNSUPPORTED_OUTER_JOIN", "SELECT * FROM a RIGHT JOIN b ON a.x = b.x;"),
    ("UNSUPPORTED_OUTER_JOIN", "SELECT * FROM a FULL OUTER JOIN b ON a.x = b.x;"),
    ("UNSUPPORTED_INHERITANCE", "CREATE TABLE c (x INT) INHERITS (p);"),
    ("UNSUPPORTED_PARTITIONING", "CREATE TABLE p (x INT) PARTITION BY RANGE (x);"),
    (
        "UNSUPPORTED_PARTITIONING",
        "CREATE TABLE p1 PARTITION OF p FOR VALUES FROM (1) TO (2);",
    ),
    (
        "UNSUPPORTED_EXCLUSION_CONSTRAINT",
        "CREATE TABLE e (x INT, EXCLUDE USING gist (x WITH =));",
    ),
    ("UNSUPPORTED_LATERAL", "SELECT * FROM a, LATERAL (SELECT 1) s;"),
    ("UNSUPPORTED_CREATE_TABLE_AS", "CREATE TABLE x AS SELECT 1;"),
    ("UNSUPPORTED_STATEMENT", "DO $$ BEGIN PERFORM 1; END $$;"),
    ("UNSUPPORTED_STATEMENT", "COPY t FROM stdin;"),
    ("UNSUPPORTED_STATEMENT", "GRANT ALL ON t TO u;"),
    ("UNSUPPORTED_STATEMENT", "DROP TABLE t;"),
    ("UNSUPPORTED_STATEMENT", "CREATE SEQUENCE s;"),
    ("UNSUPPORTED_STATEMENT", "CREATE EXTENSION pgcrypto;"),
    ("UNSUPPORTED_SET_OPERATION", "SELECT a FROM t UNION SELECT a FROM u;"),
    ("UNSUPPORTED_JOIN", "SELECT * FROM a JOIN b ON a.x > b.x;"),
    ("UNSUPPORTED_JOIN", "SELECT * FROM a CROSS JOIN b;"),
    ("UNSUPPORTED_JOIN", "SELECT * FROM a JOIN b USING (x);"),
    ("UNSUPPORTED_FUNCTION_CALL", "SELECT lower(name) FROM t;"),
    ("UNSUPPORTED_QUERY_FEATURE", "SELECT DISTINCT ON (a) a FROM t;"),
    ("UNSUPPORTED_QUERY_FEATURE", "INSERT INTO t (a) SELECT a FROM u;"),
    ("UNSUPPORTED_QUERY_FEATURE", "INSERT INTO t (a) VALUES (1) RETURNING a;"),
    ("UNSUPPORTED_QUERY_FEATURE", "INSERT INTO t (a) VALUES (1) ON CONFLICT DO NOTHING;"),
    ("UNSUPPORTED_QUERY_FEATURE", "UPDATE t SET a = 1 FROM u WHERE t.x = u.x;"),
    ("UNSUPPORTED_QUERY_FEATURE", "DELETE FROM t USING u WHERE t.x = u.x;"),
    ("UNSUPPORTED_QUERY_FEATURE", "DELETE FROM t WHERE a = 1 RETURNING a;"),
    ("UNSUPPORTED_ROLLBACK", "ROLLBACK;"),
]


@pytest.mark.parametrize(("code", "sql"), CASES, ids=[f"{c}-{i}" for i, (c, _) in enumerate(CASES)])
def test_rejected_construct(code: str, sql: str) -> None:
    result = parse(sql)
    assert code in [d.code for d in result.diagnostics], result.diagnostics
    diag = next(d for d in result.diagnostics if d.code == code)
    assert diag.severity == "error"
    assert diag.location is not None and diag.location.line_start == 1


def test_parsing_continues_after_each_rejection() -> None:
    sql = "\n".join(sql for _, sql in CASES) + "\nCREATE TABLE survivor (id INT PRIMARY KEY);"
    result = parse(sql)
    assert "survivor" in result.schema_.tables


def test_rejection_message_is_clear() -> None:
    diag = parse("CREATE VIEW v AS SELECT 1;").diagnostics[0]
    assert diag.message.startswith("Unsupported construct:")


def test_rejected_location_line_number() -> None:
    result = parse("CREATE TABLE ok (id INT);\n\nCREATE VIEW v AS SELECT 1;")
    assert result.diagnostics[0].location is not None
    assert result.diagnostics[0].location.line_start == 3
