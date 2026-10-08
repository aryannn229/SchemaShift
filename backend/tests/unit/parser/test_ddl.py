import pytest

from schemashift.parser import parse, parse_ddl, parse_queries


def codes(sql: str) -> list[str]:
    return [d.code for d in parse(sql).diagnostics]


def test_column_and_table_level_fk_are_identical() -> None:
    inline = parse(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id));"
    )
    table_level = parse(
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT, FOREIGN KEY (pid) REFERENCES p(id));"
    )
    a = inline.schema_.tables["c"].foreign_keys[0].model_copy(update={"span": None})
    b = table_level.schema_.tables["c"].foreign_keys[0].model_copy(update={"span": None})
    assert a == b


def test_alter_fk_matches_inline_fk() -> None:
    alter = parse(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT);"
        "ALTER TABLE c ADD FOREIGN KEY (pid) REFERENCES p(id) ON DELETE CASCADE;"
    )
    inline = parse(
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT REFERENCES p(id) ON DELETE CASCADE);"
    )
    a = alter.schema_.tables["c"].foreign_keys[0].model_copy(update={"span": None})
    b = inline.schema_.tables["c"].foreign_keys[0].model_copy(update={"span": None})
    assert a == b


def test_identifier_normalization() -> None:
    schema = parse('CREATE TABLE T (Id INT, "Id2" INT);').schema_
    assert list(schema.tables) == ["t"]
    assert [c.name for c in schema.tables["t"].columns] == ["id", "Id2"]


def test_quoted_table_preserved() -> None:
    assert list(parse('CREATE TABLE "Users" (id INT);').schema_.tables) == ["Users"]


def test_pk_columns_become_not_null() -> None:
    t = parse("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a));").schema_.tables["t"]
    assert [c.nullable for c in t.columns] == [False, True]


def test_explicit_null_keeps_nullable() -> None:
    t = parse("CREATE TABLE t (a INT NULL, b INT NOT NULL);").schema_.tables["t"]
    assert [c.nullable for c in t.columns] == [True, False]


def test_implicit_fk_target_resolves_to_parent_pk() -> None:
    schema = parse(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p);"
    ).schema_
    assert schema.tables["c"].foreign_keys[0].ref_columns == ("id",)


def test_implicit_fk_target_unknown_parent_stays_empty() -> None:
    schema = parse("CREATE TABLE c (pid INT REFERENCES nope);").schema_
    assert schema.tables["c"].foreign_keys[0].ref_columns == ()


def test_serial_family_is_identity() -> None:
    t = parse("CREATE TABLE t (a SERIAL, b BIGSERIAL, c SMALLSERIAL);").schema_.tables["t"]
    assert [c.is_identity for c in t.columns] == [True, True, True]
    assert [c.sql_type.base for c in t.columns] == ["INTEGER", "BIGINT", "SMALLINT"]


def test_default_kinds() -> None:
    t = parse(
        "CREATE TABLE t (a INT DEFAULT 1, b TIMESTAMPTZ DEFAULT now(), c INT DEFAULT (1+2));"
    ).schema_.tables["t"]
    assert [c.default.kind for c in t.columns if c.default] == ["literal", "now", "expression"]


def test_duplicate_table_reported() -> None:
    assert "SEM006" in codes("CREATE TABLE a (x INT); CREATE TABLE a (y INT);")


def test_duplicate_primary_key_reported() -> None:
    assert "PARSE005" in codes("CREATE TABLE t (a INT PRIMARY KEY, b INT, PRIMARY KEY (b));")


def test_unknown_enum_type_reported() -> None:
    assert "PARSE008" in codes("CREATE TABLE t (a nope_type);")


def test_unsupported_column_type_reported_and_continues() -> None:
    result = parse("CREATE TABLE t (a INT, b MONEY); CREATE TABLE u (x INT);")
    assert "PARSE007" in [d.code for d in result.diagnostics]
    assert "u" in result.schema_.tables
    assert [c.name for c in result.schema_.tables["t"].columns] == ["a"]


def test_alter_unknown_table() -> None:
    assert "PARSE006" in codes("ALTER TABLE ghost ADD PRIMARY KEY (id);")


def test_index_unknown_table() -> None:
    assert "PARSE006" in codes("CREATE INDEX i ON ghost (a);")


def test_expression_index_rejected() -> None:
    assert "UNSUPPORTED_EXPRESSION_INDEX" in codes(
        "CREATE TABLE t (a TEXT); CREATE INDEX i ON t (lower(a));"
    )


def test_partial_unique_index() -> None:
    t = parse(
        "CREATE TABLE t (a TEXT, d BOOL); CREATE UNIQUE INDEX i ON t (a) WHERE d = false;"
    ).schema_.tables["t"]
    assert t.indexes[0].unique and t.indexes[0].where_sql == "d = FALSE"


def test_enum_values() -> None:
    schema = parse("CREATE TYPE m AS ENUM ('a', 'b');").schema_
    assert schema.enums["m"].values == ("a", "b")


def test_duplicate_enum() -> None:
    assert "PARSE009" in codes("CREATE TYPE m AS ENUM ('a'); CREATE TYPE m AS ENUM ('b');")


def test_composite_type_unsupported() -> None:
    assert "UNSUPPORTED_STATEMENT" in codes("CREATE TYPE pt AS (x INT, y INT);")


def test_syntax_error_has_line_and_column() -> None:
    result = parse("CREATE TABLE ok (id INT);\nCREATE TABLE t (id INT PRIMARY KEY) garbage;")
    diag = next(d for d in result.diagnostics if d.code == "PARSE001")
    assert diag.location is not None and diag.location.line_start == 2
    assert "ok" in result.schema_.tables


def test_syntax_error_column_points_at_token() -> None:
    diag = parse("SELECT a FROM WHERE;").diagnostics[0]
    assert diag.location is not None
    assert (diag.location.line_start, diag.location.col_start) == (1, 15)
    assert "'WHERE'" in diag.message and "<Token" not in diag.message


def test_syntax_error_position_on_later_line() -> None:
    diag = parse("SELECT *\nFROM t\nWHERE (a = ;").diagnostics[0]
    assert diag.location is not None and diag.location.line_start == 3


def test_empty_list_element_is_error() -> None:
    assert codes("CREATE TABLE bad (id INT,, );") == ["PARSE001"]


def test_bare_name_element_is_error() -> None:
    assert codes("CREATE TABLE bad (id INT, name);") == ["PARSE001"]


def test_unterminated_string_is_error() -> None:
    assert "PARSE001" in codes("CREATE TABLE t (a TEXT DEFAULT 'oops);")


def test_unrecognized_statement() -> None:
    assert codes("FROBNICATE everything;") == ["PARSE002"]


def test_not_deferrable_and_initially_accepted() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT REFERENCES p(id) NOT DEFERRABLE INITIALLY IMMEDIATE);"
    )
    result = parse(sql)
    assert not result.has_errors
    assert result.schema_.tables["c"].foreign_keys[0].deferrable is False


def test_deferrable_detected() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (pid INT REFERENCES p(id) DEFERRABLE);"
    )
    assert parse(sql).schema_.tables["c"].foreign_keys[0].deferrable is True


def test_spans_point_at_column_definition() -> None:
    t = parse("CREATE TABLE t (\n  a INT,\n  b TEXT NOT NULL\n);").schema_.tables["t"]
    assert t.columns[1].span is not None
    assert (t.columns[1].span.line_start, t.columns[1].span.col_start) == (3, 3)
    assert t.span is not None and t.span.line_start == 1 and t.span.line_end == 4


def test_check_constraint_roundtrip_parse() -> None:
    t = parse("CREATE TABLE t (a INT CHECK (a > 0));").schema_.tables["t"]
    assert t.checks[0].parse().sql() == "a > 0"


def test_mode_ddl_ignores_queries() -> None:
    result = parse_ddl("CREATE TABLE t (a INT); SELECT * FROM t;")
    assert result.queries == ()
    assert [d.code for d in result.diagnostics] == ["PARSE003"]


def test_mode_queries_ignores_ddl() -> None:
    result = parse_queries("CREATE TABLE t (a INT); SELECT * FROM t;")
    assert result.schema_.tables == {}
    assert len(result.queries) == 1


def test_alter_partial_failure_keeps_valid_actions() -> None:
    result = parse(
        "CREATE TABLE t (a INT, b INT); ALTER TABLE t ADD PRIMARY KEY (a), ADD COLUMN c INT;"
    )
    assert result.schema_.tables["t"].primary_key is not None
    assert "UNSUPPORTED_ALTER_ACTION" in [d.code for d in result.diagnostics]


def test_non_alter_table_alter_unsupported() -> None:
    assert codes("ALTER TYPE x ADD VALUE 'y';") == ["UNSUPPORTED_STATEMENT"]


def test_malformed_alter_table() -> None:
    assert "PARSE001" in codes("ALTER TABLE;")


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment", "/* block */;;"])
def test_empty_inputs(sql: str) -> None:
    result = parse(sql)
    assert result.schema_.tables == {} and result.diagnostics == ()
