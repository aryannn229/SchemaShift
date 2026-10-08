from decimal import Decimal

import pytest
import sqlglot

from schemashift.parser import parse
from schemashift.verification.ddl import (
    UnsafeSqlError,
    assert_safe_expression,
    assert_safe_sql,
    ddl_statements,
    q,
    type_sql,
)
from schemashift.verification.sqleval import Unevaluable, evaluate, passes_check
from tests.unit.codegen.helpers import SAMPLE_NAMES, sample

SQL = """
CREATE TYPE mood AS ENUM ('a', 'it''s');
CREATE TABLE "Odd Name" (
  id SERIAL PRIMARY KEY, m mood NOT NULL DEFAULT 'a', price NUMERIC(10,2) CHECK (price >= 0),
  tags TEXT[], created TIMESTAMPTZ DEFAULT now(), u UUID DEFAULT gen_random_uuid(), CONSTRAINT uq UNIQUE (m, price)
);
CREATE TABLE child (id INT PRIMARY KEY, p INT REFERENCES "Odd Name"(id) ON DELETE CASCADE ON UPDATE SET NULL);
CREATE UNIQUE INDEX ix ON child (p) WHERE id > 1;
"""


def roundtrip(sql: str):  # type: ignore[no-untyped-def]
    schema = parse(sql).schema_
    stmts, warnings = ddl_statements(schema)
    again = parse(";\n".join(stmts) + ";")
    assert not again.has_errors, again.diagnostics
    return schema, again.schema_, warnings


def strip(table):  # type: ignore[no-untyped-def]
    return (
        [(c.name, c.sql_type, c.nullable, c.is_identity) for c in table.columns],
        table.primary_key.columns if table.primary_key else None,
        [
            (f.columns, f.ref_table, f.ref_columns, f.on_delete, f.on_update)
            for f in table.foreign_keys
        ],
        [u.columns for u in table.uniques],
        [c.expression_sql for c in table.checks],
        [(i.columns, i.unique, i.where_sql) for i in table.indexes],
    )


def test_emitted_ddl_parses_back_to_the_same_schema() -> None:
    original, again, warnings = roundtrip(SQL)
    assert warnings == []
    assert original.enums["mood"].values == again.enums["mood"].values == ("a", "it's")
    for name in original.tables:
        assert strip(original.tables[name]) == strip(again.tables[name]), name


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_sample_ddl_roundtrips(name: str) -> None:
    schema_sql, _, _ = sample(name)
    original, again, _ = roundtrip(schema_sql)
    assert set(original.tables) == set(again.tables)
    for table in original.tables:
        assert strip(original.tables[table]) == strip(again.tables[table]), table


def test_identifiers_are_always_quoted() -> None:
    assert q('we"ird') == '"we""ird"'
    stmts, _ = ddl_statements(parse(SQL).schema_)
    assert 'CREATE TABLE "Odd Name"' in "\n".join(stmts)
    assert all('"' in s for s in stmts)


def test_order_is_types_tables_foreign_keys_indexes() -> None:
    stmts, _ = ddl_statements(parse(SQL).schema_)
    kinds = [s.split(" ", 2)[0] + " " + s.split(" ", 2)[1] for s in stmts]
    assert kinds == ["CREATE TYPE", "CREATE TABLE", "CREATE TABLE", "ALTER TABLE", "CREATE UNIQUE"]


def test_circular_foreign_keys_use_alter_table() -> None:
    sql = "CREATE TABLE a (id INT PRIMARY KEY, b INT); CREATE TABLE b (id INT PRIMARY KEY, a INT REFERENCES a(id)); ALTER TABLE a ADD FOREIGN KEY (b) REFERENCES b(id);"
    stmts, _ = ddl_statements(parse(sql).schema_)
    assert sum(s.startswith("ALTER TABLE") for s in stmts) == 2


def test_type_sql() -> None:
    types = {
        "NUMERIC(10,2)": "NUMERIC(10,2)",
        "REAL": "REAL",
        "DOUBLE PRECISION": "DOUBLE PRECISION",
        "VARCHAR(5)": "VARCHAR(5)",
        "CHAR(2)": "CHAR(2)",
        "INT[]": "INTEGER[]",
        "TIMESTAMPTZ": "TIMESTAMPTZ",
    }
    for source, expected in types.items():
        col = parse(f"CREATE TABLE t (c {source});").schema_.tables["t"].columns[0]
        assert type_sql(col.sql_type) == expected


# -------------------------------------------------------------------- whitelist
@pytest.mark.parametrize(
    "sql",
    [
        "pg_sleep(10) > 0",
        "set_config('x', 'y', false) = 'y'",
        "lo_import('/etc/passwd') > 0",
        "(SELECT count(*) FROM pg_shadow) > 0",
        "dblink('x', 'y') = 'z'",
        "current_setting('x') = 'y'",
        "a IN (SELECT 1)",
        "pg_catalog.length(a) > 0",
    ],
)
def test_unsafe_expressions_are_rejected(sql: str) -> None:
    with pytest.raises(UnsafeSqlError):
        assert_safe_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "a > 0 AND b IN (1, 2)",
        "UPPER(c) = c",
        "LENGTH(c) > 2",
        "COALESCE(a, 0) >= 0",
        "a BETWEEN 1 AND 2",
    ],
)
def test_safe_expressions_are_accepted(sql: str) -> None:
    assert_safe_sql(sql)


def test_qualified_and_unknown_tables_are_rejected() -> None:
    schema = parse("CREATE TABLE t (a INT);").schema_
    with pytest.raises(UnsafeSqlError):
        assert_safe_expression(
            sqlglot.parse_one("SELECT * FROM pg_catalog.pg_tables", dialect="postgres"), schema
        )
    with pytest.raises(UnsafeSqlError):
        assert_safe_expression(sqlglot.parse_one("SELECT * FROM other", dialect="postgres"), schema)
    assert_safe_expression(
        sqlglot.parse_one("SELECT a FROM t WHERE a > 1", dialect="postgres"), schema
    )


def test_unsafe_default_and_check_are_dropped_or_rejected() -> None:
    bad_default = parse("CREATE TABLE t (a INT DEFAULT pg_sleep(1));").schema_
    with pytest.raises(UnsafeSqlError):
        ddl_statements(bad_default)
    stmts, warnings = ddl_statements(
        parse("CREATE TABLE t (a INT, CHECK (pg_sleep(1) IS NULL));").schema_
    )
    assert warnings and "CHECK" not in "\n".join(stmts)
    stmts, warnings = ddl_statements(
        parse("CREATE TABLE t (a INT); CREATE INDEX ix ON t (a) WHERE pg_sleep(1) IS NULL;").schema_
    )
    assert warnings and "CREATE INDEX" not in "\n".join(stmts)


def test_unparseable_expression() -> None:
    with pytest.raises(UnsafeSqlError):
        assert_safe_sql("a >>> ((")


# --------------------------------------------------------------------- sqleval
def ev(sql: str, **row):  # type: ignore[no-untyped-def]
    return evaluate(sqlglot.parse_one(sql, dialect="postgres"), row)


def test_three_valued_logic() -> None:
    assert ev("a > 1", a=2) is True and ev("a > 1", a=0) is False and ev("a > 1", a=None) is None
    assert ev("a > 1 AND b > 1", a=0, b=None) is False
    assert ev("a > 1 AND b > 1", a=2, b=None) is None
    assert ev("a > 1 OR b > 1", a=2, b=None) is True
    assert ev("a > 1 OR b > 1", a=0, b=None) is None
    assert ev("NOT (a > 1)", a=None) is None


def test_predicates_and_functions() -> None:
    assert ev("a IN (1, 2)", a=2) is True and ev("a IN (1, NULL)", a=5) is None
    assert ev("a NOT IN (1, 2)", a=5) is True and ev("a NOT IN (1, 2)", a=1) is False
    assert ev("a BETWEEN 1 AND 3", a=2) is True and ev("a BETWEEN 1 AND 3", a=None) is None
    assert ev("a IS NULL", a=None) is True and ev("a IS NOT NULL", a=None) is False
    assert ev("a LIKE 'x%'", a="xyz") is True and ev("a NOT LIKE 'x%'", a="xyz") is False
    assert ev("a ILIKE 'X%'", a="xyz") is True
    assert ev("LENGTH(a) > 2", a="abc") is True and ev("UPPER(a) = 'AB'", a="ab") is True
    assert ev("LOWER(a) = 'ab'", a="AB") is True and ev("ABS(a) = 3", a=-3) is True
    assert ev("COALESCE(a, 7) = 7", a=None) is True


def test_arithmetic_and_decimals() -> None:
    assert ev("a + b * 2 = 7", a=1, b=3) is True
    assert ev("a * 2 > 1.5", a=Decimal("1.00")) is True
    assert ev("a / b = 2", a=4, b=2) is True and ev("a / b", a=1, b=0) is None
    assert ev("-a = 2", a=-2) is True and ev("a - 1 = 1", a=Decimal("2")) is True
    assert ev("a > 1.5", a=2.0) is True  # float vs decimal literal


def test_check_semantics() -> None:
    node = sqlglot.parse_one("a > 0", dialect="postgres")
    assert (
        passes_check(node, {"a": 1})
        and passes_check(node, {"a": None})
        and not passes_check(node, {"a": -1})
    )
    assert passes_check(
        sqlglot.parse_one("a IN (SELECT 1)", dialect="postgres"), {"a": 1}
    )  # unknown syntax passes
    assert passes_check(
        sqlglot.parse_one("a > 'x'", dialect="postgres"), {"a": 1}
    )  # type error passes


def test_unsupported_nodes_raise() -> None:
    with pytest.raises(Unevaluable):
        ev("a IS TRUE", a=True)
    with pytest.raises(Unevaluable):
        ev("EXTRACT(YEAR FROM a) > 1", a=1)
