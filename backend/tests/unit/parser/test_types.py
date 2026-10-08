import pytest
from sqlglot import exp

from schemashift.models import NormalizedType
from schemashift.parser import parse
from schemashift.parser.types import UnsupportedTypeError, normalize_type


def col_type(sql_type: str) -> NormalizedType:
    t = parse(f"CREATE TABLE t (c {sql_type});").schema_.tables["t"]
    return t.columns[0].sql_type


@pytest.mark.parametrize(
    ("sql_type", "base"),
    [
        ("INT", "INTEGER"),
        ("INT4", "INTEGER"),
        ("SMALLINT", "SMALLINT"),
        ("INT2", "SMALLINT"),
        ("BIGINT", "BIGINT"),
        ("INT8", "BIGINT"),
        ("NUMERIC", "DECIMAL"),
        ("REAL", "FLOAT"),
        ("DOUBLE PRECISION", "DOUBLE"),
        ("FLOAT8", "DOUBLE"),
        ("BOOLEAN", "BOOLEAN"),
        ("BOOL", "BOOLEAN"),
        ("TEXT", "TEXT"),
        ("VARCHAR", "VARCHAR"),
        ("CHARACTER VARYING(5)", "VARCHAR"),
        ("CHAR(2)", "CHAR"),
        ("BPCHAR", "CHAR"),
        ("DATE", "DATE"),
        ("TIMESTAMP", "TIMESTAMP"),
        ("TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP"),
        ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"),
        ("TIMESTAMPTZ", "TIMESTAMPTZ"),
        ("TIME", "TIME"),
        ("TIMETZ", "TIME"),
        ("UUID", "UUID"),
        ("JSON", "JSON"),
        ("JSONB", "JSONB"),
        ("BYTEA", "BYTEA"),
        ("INTERVAL", "INTERVAL"),
    ],
)
def test_base_mapping(sql_type: str, base: str) -> None:
    assert col_type(sql_type).base == base


def test_varchar_length() -> None:
    assert col_type("VARCHAR(40)").length == 40
    assert col_type("VARCHAR").length is None


def test_decimal_precision_scale() -> None:
    t = col_type("NUMERIC(10, 2)")
    assert (t.precision, t.scale) == (10, 2)
    t = col_type("DECIMAL(5)")
    assert (t.precision, t.scale) == (5, 0)
    t = col_type("NUMERIC")
    assert (t.precision, t.scale) == (None, None)


def test_arrays() -> None:
    assert col_type("INT[]").is_array and col_type("INT[]").base == "INTEGER"
    assert col_type("TEXT[][]").is_array
    assert not col_type("INT").is_array


def test_unsupported_type_raises() -> None:
    dt = exp.DataType.build("MONEY", dialect="postgres")
    with pytest.raises(UnsupportedTypeError):
        normalize_type(dt)


def test_array_without_element_type_raises() -> None:
    with pytest.raises(UnsupportedTypeError):
        normalize_type(exp.DataType(this=exp.DType.ARRAY))
