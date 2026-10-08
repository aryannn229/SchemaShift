"""Each SEM code has positive (fires) and negative (does not fire) tests."""

import pytest

from schemashift.parser import parse
from schemashift.semantic import analyze

PARENT = "CREATE TABLE p (id INT PRIMARY KEY, code TEXT UNIQUE);\n"


def codes(sql: str) -> list[str]:
    result = parse(sql)
    analysis = analyze(result.schema_, result.queries)
    return [d.code for d in analysis.diagnostics]


def severities(sql: str, code: str) -> list[str]:
    result = parse(sql)
    return [
        d.severity for d in analyze(result.schema_, result.queries).diagnostics if d.code == code
    ]


# SEM001 ---------------------------------------------------------------------------------
def test_sem001_positive() -> None:
    assert "SEM001" in codes("CREATE TABLE c (pid INT REFERENCES ghost(id));")


def test_sem001_negative() -> None:
    assert "SEM001" not in codes(PARENT + "CREATE TABLE c (pid INT REFERENCES p(id));")


# SEM002 ---------------------------------------------------------------------------------
def test_sem002_parent_column_missing() -> None:
    assert "SEM002" in codes(PARENT + "CREATE TABLE c (pid INT REFERENCES p(nope));")


def test_sem002_child_column_missing() -> None:
    sql = PARENT + "CREATE TABLE c (x INT, FOREIGN KEY (nope) REFERENCES p(id));"
    assert "SEM002" in codes(sql)


def test_sem002_negative() -> None:
    assert "SEM002" not in codes(PARENT + "CREATE TABLE c (pid INT REFERENCES p(id));")


# SEM003 ---------------------------------------------------------------------------------
def test_sem003_positive() -> None:
    sql = (
        "CREATE TABLE p2 (a INT, b INT, PRIMARY KEY (a, b));"
        "CREATE TABLE c (x INT, FOREIGN KEY (x) REFERENCES p2(a, b));"
    )
    assert "SEM003" in codes(sql)


def test_sem003_negative() -> None:
    sql = (
        "CREATE TABLE p2 (a INT, b INT, PRIMARY KEY (a, b));"
        "CREATE TABLE c (x INT, y INT, FOREIGN KEY (x, y) REFERENCES p2(a, b));"
    )
    assert "SEM003" not in codes(sql)


# SEM004 ---------------------------------------------------------------------------------
def test_sem004_mismatch_is_error() -> None:
    sql = PARENT + "CREATE TABLE c (pid TEXT REFERENCES p(id));"
    assert severities(sql, "SEM004") == ["error"]


def test_sem004_widening_is_warning() -> None:
    sql = "CREATE TABLE p (id BIGINT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id));"
    assert severities(sql, "SEM004") == ["warning"]


def test_sem004_text_family_ok() -> None:
    sql = "CREATE TABLE p (id VARCHAR(5) PRIMARY KEY);CREATE TABLE c (pid TEXT REFERENCES p(id));"
    assert "SEM004" not in codes(sql)


def test_sem004_array_mismatch() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT[] REFERENCES p(id));"
    assert "SEM004" in codes(sql)


def test_sem004_enum_names_must_match() -> None:
    sql = (
        "CREATE TYPE e1 AS ENUM ('a'); CREATE TYPE e2 AS ENUM ('a');"
        "CREATE TABLE p (id e1 PRIMARY KEY); CREATE TABLE c (pid e2 REFERENCES p(id));"
    )
    assert "SEM004" in codes(sql)


def test_sem004_same_enum_ok() -> None:
    sql = (
        "CREATE TYPE e1 AS ENUM ('a');"
        "CREATE TABLE p (id e1 PRIMARY KEY); CREATE TABLE c (pid e1 REFERENCES p(id));"
    )
    assert "SEM004" not in codes(sql)


def test_sem004_exact_match_ok() -> None:
    assert "SEM004" not in codes(PARENT + "CREATE TABLE c (pid INT REFERENCES p(id));")


# SEM005 ---------------------------------------------------------------------------------
def test_sem005_not_a_key() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY, name TEXT); CREATE TABLE c (n TEXT REFERENCES p(name));"
    assert "SEM005" in codes(sql)


def test_sem005_unique_target_ok() -> None:
    sql = PARENT + "CREATE TABLE c (n TEXT REFERENCES p(code));"
    assert "SEM005" not in codes(sql)


def test_sem005_unique_index_target_ok() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY, name TEXT); CREATE UNIQUE INDEX u ON p (name);"
        "CREATE TABLE c (n TEXT REFERENCES p(name));"
    )
    assert "SEM005" not in codes(sql)


def test_sem005_partial_unique_index_is_not_a_key() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY, name TEXT); "
        "CREATE UNIQUE INDEX u ON p (name) WHERE id > 1;"
        "CREATE TABLE c (n TEXT REFERENCES p(name));"
    )
    assert "SEM005" in codes(sql)


def test_sem005_composite_subset_is_not_unique() -> None:
    sql = (
        "CREATE TABLE p (a INT, b INT, PRIMARY KEY (a, b));CREATE TABLE c (x INT REFERENCES p(a));"
    )
    assert "SEM005" in codes(sql)


def test_sem005_implicit_target_without_pk() -> None:
    sql = "CREATE TABLE p (id INT); CREATE TABLE c (x INT REFERENCES p);"
    assert "SEM005" in codes(sql)


# SEM006 ---------------------------------------------------------------------------------
def test_sem006_duplicate_column() -> None:
    assert "SEM006" in codes("CREATE TABLE t (a INT, a TEXT);")


def test_sem006_duplicate_table_from_parser() -> None:
    result = parse("CREATE TABLE t (a INT); CREATE TABLE t (b INT);")
    assert "SEM006" in [d.code for d in result.diagnostics]


def test_sem006_negative() -> None:
    assert "SEM006" not in codes("CREATE TABLE t (a INT, b INT);")


# SEM007 ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (a INT, PRIMARY KEY (zz));",
        "CREATE TABLE t (a INT, UNIQUE (zz));",
        "CREATE TABLE t (a INT); CREATE INDEX i ON t (zz);",
    ],
)
def test_sem007_positive(sql: str) -> None:
    assert "SEM007" in codes(sql)


def test_sem007_negative() -> None:
    sql = "CREATE TABLE t (a INT, PRIMARY KEY (a), UNIQUE (a)); CREATE INDEX i ON t (a);"
    assert "SEM007" not in codes(sql)


# SEM008 ---------------------------------------------------------------------------------
def test_sem008_positive() -> None:
    assert severities("CREATE TABLE t (a INT);", "SEM008") == ["warning"]


def test_sem008_negative() -> None:
    assert "SEM008" not in codes("CREATE TABLE t (a INT PRIMARY KEY);")


# SEM009 ---------------------------------------------------------------------------------
def test_sem009_two_cycle() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id);"
    )
    result = parse(sql)
    diags = [d for d in analyze(result.schema_).diagnostics if d.code == "SEM009"]
    assert len(diags) == 1 and "a -> b -> a" in diags[0].message


def test_sem009_three_cycle_reports_path() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, c_id INT);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id));"
        "ALTER TABLE a ADD FOREIGN KEY (c_id) REFERENCES c(id);"
    )
    result = parse(sql)
    diags = [d for d in analyze(result.schema_).diagnostics if d.code == "SEM009"]
    assert len(diags) == 1 and "a -> c -> b -> a" in diags[0].message


def test_sem009_self_cycle_is_not_sem009() -> None:
    sql = "CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));"
    found = codes(sql)
    assert "SEM009" not in found and "SEM011" in found


def test_sem009_negative_chain() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id));"
    )
    assert "SEM009" not in codes(sql)


# SEM010 ---------------------------------------------------------------------------------
def test_sem010_positive() -> None:
    sql = PARENT + "CREATE TABLE c (pid INT NOT NULL REFERENCES p(id) ON DELETE SET NULL);"
    assert severities(sql, "SEM010") == ["warning"]


def test_sem010_negative_nullable() -> None:
    sql = PARENT + "CREATE TABLE c (pid INT REFERENCES p(id) ON DELETE SET NULL);"
    assert "SEM010" not in codes(sql)


def test_sem010_negative_other_action() -> None:
    sql = PARENT + "CREATE TABLE c (pid INT NOT NULL REFERENCES p(id) ON DELETE CASCADE);"
    assert "SEM010" not in codes(sql)


# SEM011 ---------------------------------------------------------------------------------
def test_sem011_positive() -> None:
    sql = "CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));"
    assert severities(sql, "SEM011") == ["info"]


def test_sem011_negative() -> None:
    assert "SEM011" not in codes(PARENT + "CREATE TABLE c (pid INT REFERENCES p(id));")


# SEM012 ---------------------------------------------------------------------------------
SCHEMA = (
    "CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);"
    "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customers(id), total INT);"
)


def test_sem012_unknown_table() -> None:
    assert "SEM012" in codes(SCHEMA + "SELECT * FROM ghosts;")


def test_sem012_unknown_column() -> None:
    assert "SEM012" in codes(SCHEMA + "SELECT nope FROM customers;")


def test_sem012_unknown_qualified_column() -> None:
    assert "SEM012" in codes(SCHEMA + "SELECT c.nope FROM customers c;")


def test_sem012_unknown_alias() -> None:
    assert "SEM012" in codes(SCHEMA + "SELECT x.id FROM customers c;")


def test_sem012_ambiguous_column() -> None:
    sql = SCHEMA + "SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id;"
    assert "SEM012" in codes(sql)


def test_sem012_insert_unknown_column() -> None:
    assert "SEM012" in codes(SCHEMA + "INSERT INTO customers (id, bogus) VALUES (1, 'x');")


def test_sem012_insert_ok() -> None:
    assert "SEM012" not in codes(SCHEMA + "INSERT INTO customers (id, name) VALUES (1, 'x');")


def test_sem012_update_and_delete() -> None:
    assert "SEM012" in codes(SCHEMA + "UPDATE customers SET bogus = 1 WHERE id = 1;")
    assert "SEM012" in codes(SCHEMA + "DELETE FROM customers WHERE bogus = 1;")


def test_sem012_valid_queries() -> None:
    sql = SCHEMA + (
        "SELECT c.name, COUNT(*) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
        "WHERE c.id > 1 GROUP BY c.name HAVING COUNT(*) > 1 ORDER BY n;"
        "SELECT * FROM customers;"
        "UPDATE customers SET name = 'x' WHERE id = 1;"
        "BEGIN; DELETE FROM orders WHERE id = 1; COMMIT;"
    )
    assert "SEM012" not in codes(sql)


def test_sem012_inside_transaction() -> None:
    assert "SEM012" in codes(SCHEMA + "BEGIN; DELETE FROM ghosts WHERE id = 1; COMMIT;")


def test_diagnostics_are_sorted_by_location() -> None:
    sql = "CREATE TABLE b (x INT);\nCREATE TABLE a (y INT);"
    result = parse(sql)
    lines = [d.location.line_start for d in analyze(result.schema_).diagnostics if d.location]
    assert lines == sorted(lines)


def test_clean_schema_has_no_diagnostics() -> None:
    sql = PARENT + "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id));"
    assert codes(sql) == []
