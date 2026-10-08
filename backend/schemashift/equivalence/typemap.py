"""The Postgres -> BSON type map, shared by the equivalence rules and code generation."""

from __future__ import annotations

from dataclasses import dataclass

from schemashift.models.schema import NormalizedType, TypeBase


@dataclass(frozen=True)
class TypeMapping:
    bson: str  # $jsonSchema bsonType
    note: str  # why it is exact / lossy
    lossy: bool = False  # always lossy for this base type
    fix: str = ""  # how to compensate when lossy


TYPE_MAP: dict[TypeBase, TypeMapping] = {
    "SMALLINT": TypeMapping("int", "stored as int with minimum/maximum for the SMALLINT range"),
    "INTEGER": TypeMapping("int", "stored as int"),
    "BIGINT": TypeMapping("long", "stored as long"),
    "DECIMAL": TypeMapping("decimal", "stored as Decimal128; precision/scale are not enforced"),
    "FLOAT": TypeMapping("double", "stored as double"),
    "DOUBLE": TypeMapping("double", "stored as double"),
    "BOOLEAN": TypeMapping("bool", "stored as bool"),
    "TEXT": TypeMapping("string", "stored as string"),
    "VARCHAR": TypeMapping("string", "stored as string with maxLength"),
    "CHAR": TypeMapping(
        "string",
        "CHAR(n) space padding is not preserved",
        lossy=True,
        fix="pad or trim values in the application layer",
    ),
    "UUID": TypeMapping("binData", "stored as binData subtype 4 (or string when configured)"),
    "DATE": TypeMapping(
        "date",
        "BSON date adds a midnight-UTC time component",
        lossy=True,
        fix="treat the field as date-only in application code and normalize it to midnight UTC",
    ),
    "TIMESTAMP": TypeMapping("date", "BSON date keeps millisecond precision"),
    "TIMESTAMPTZ": TypeMapping(
        "date",
        "BSON date stores UTC only, the UTC offset is lost",
        lossy=True,
        fix="store the original offset in a companion field if it matters",
    ),
    "TIME": TypeMapping(
        "string",
        "no BSON time-of-day type, stored as a string",
        lossy=True,
        fix="validate the format and parse the string in application code",
    ),
    "INTERVAL": TypeMapping(
        "string",
        "no BSON interval type, stored as a string",
        lossy=True,
        fix="store an ISO-8601 duration string and parse it in application code",
    ),
    "JSON": TypeMapping("object", "stored as an embedded document"),
    "JSONB": TypeMapping("object", "stored as an embedded document"),
    "BYTEA": TypeMapping("binData", "stored as binData"),
    "ENUM": TypeMapping("string", "stored as string with an enum list"),
}


@dataclass(frozen=True)
class TypeInfo:
    bson: str
    lossy: bool
    note: str
    fix: str


def type_info(t: NormalizedType) -> TypeInfo:
    """BSON type and whether the mapping loses a guarantee for this exact column type."""
    mapping = TYPE_MAP[t.base]
    lossy = mapping.lossy
    note = mapping.note
    fix = mapping.fix
    if t.base == "DECIMAL" and t.scale is not None and t.scale > 0:
        lossy = True
        note = f"Decimal128 does not enforce scale {t.scale} (NUMERIC({t.precision},{t.scale}))"
        fix = "round and validate the scale in the application layer before writing"
    bson = f"array<{mapping.bson}>" if t.is_array else mapping.bson
    return TypeInfo(bson=bson, lossy=lossy, note=note, fix=fix)
