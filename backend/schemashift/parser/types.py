"""SQL type normalization (sqlglot ``DataType`` -> ``NormalizedType``)."""

from __future__ import annotations

from sqlglot import exp
from sqlglot.expressions import DType

from schemashift.models.schema import NormalizedType, TypeBase

_SIMPLE: dict[DType, TypeBase] = {
    DType.INT: "INTEGER",
    DType.SMALLINT: "SMALLINT",
    DType.BIGINT: "BIGINT",
    DType.DECIMAL: "DECIMAL",
    DType.FLOAT: "FLOAT",
    DType.DOUBLE: "DOUBLE",
    DType.BOOLEAN: "BOOLEAN",
    DType.TEXT: "TEXT",
    DType.VARCHAR: "VARCHAR",
    DType.CHAR: "CHAR",
    DType.BPCHAR: "CHAR",
    DType.DATE: "DATE",
    DType.TIMESTAMP: "TIMESTAMP",
    DType.TIMESTAMPTZ: "TIMESTAMPTZ",
    DType.TIME: "TIME",
    DType.TIMETZ: "TIME",
    DType.UUID: "UUID",
    DType.JSON: "JSON",
    DType.JSONB: "JSONB",
    DType.VARBINARY: "BYTEA",
    DType.BINARY: "BYTEA",
    DType.INTERVAL: "INTERVAL",
}

_SERIAL: dict[DType, TypeBase] = {
    DType.SERIAL: "INTEGER",
    DType.SMALLSERIAL: "SMALLINT",
    DType.BIGSERIAL: "BIGINT",
}


class UnsupportedTypeError(Exception):
    def __init__(self, type_sql: str) -> None:
        super().__init__(f"Unsupported column type: {type_sql}")
        self.type_sql = type_sql


def _int_params(dt: exp.DataType) -> list[int]:
    values: list[int] = []
    for param in dt.expressions:
        node = param.this if isinstance(param, exp.DataTypeParam) else param
        if isinstance(node, exp.Literal) and not node.is_string:
            values.append(int(node.this))
    return values


def _user_defined_name(dt: exp.DataType) -> str:
    raw = dt.sql(dialect="postgres").strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    return raw.lower()


def normalize_type(dt: exp.DataType) -> tuple[NormalizedType, bool]:
    """Return the normalized type and whether it implies auto-increment (SERIAL)."""
    is_array = False
    while dt.this == DType.ARRAY:
        is_array = True
        inner = dt.expressions[0] if dt.expressions else None
        if not isinstance(inner, exp.DataType):
            raise UnsupportedTypeError(dt.sql(dialect="postgres"))
        dt = inner

    kind = dt.this
    params = _int_params(dt)
    if kind in _SERIAL:
        return NormalizedType(base=_SERIAL[kind], is_array=is_array), True
    if kind == DType.USERDEFINED:
        return (
            NormalizedType(base="ENUM", enum_name=_user_defined_name(dt), is_array=is_array),
            False,
        )
    if kind in _SIMPLE:
        base = _SIMPLE[kind]
        length = params[0] if base in ("VARCHAR", "CHAR") and params else None
        precision = params[0] if base == "DECIMAL" and params else None
        scale = params[1] if base == "DECIMAL" and len(params) > 1 else None
        if base == "DECIMAL" and precision is not None and scale is None:
            scale = 0
        return (
            NormalizedType(
                base=base, length=length, precision=precision, scale=scale, is_array=is_array
            ),
            False,
        )
    raise UnsupportedTypeError(dt.sql(dialect="postgres"))
