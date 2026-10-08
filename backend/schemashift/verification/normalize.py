"""Normalize PostgreSQL and MongoDB result values to one comparable representation."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from bson import Binary, Decimal128, Int64, ObjectId

TOLERANCE = 1e-9


def duration_text(value: dt.timedelta) -> str:
    """Same text the migration stores for INTERVAL values."""
    return f"PT{value.total_seconds():g}S"


def normalize_value(value: Any) -> Any:
    """Canonical form: Decimal for numbers, UTC ms datetimes, UUIDs, plain JSON containers."""
    if isinstance(value, Binary):  # before bytes: Binary is a bytes subclass
        return value.as_uuid() if value.subtype == 4 else bytes(value)
    if value is None or isinstance(value, (bool, str, bytes)):
        return value
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, (int, Int64)):
        return Decimal(int(value))
    if isinstance(value, float):
        return Decimal(repr(value)) if value == value and abs(value) != float("inf") else value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, dt.datetime):
        aware = value.astimezone(dt.UTC) if value.tzinfo else value.replace(tzinfo=dt.UTC)
        return aware.replace(microsecond=(aware.microsecond // 1000) * 1000)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.UTC)
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return duration_text(value)
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, Binary):
        return value.as_uuid() if value.subtype == 4 else bytes(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    return value


def normalize_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(normalize_value(v) for v in row)


def values_equal(a: Any, b: Any, tolerance: float = TOLERANCE) -> bool:
    if isinstance(a, (Decimal, float)) and isinstance(b, (Decimal, float)):
        x, y = float(a), float(b)
        return abs(x - y) <= tolerance * max(1.0, abs(x), abs(y))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(
            values_equal(x, y, tolerance) for x, y in zip(a, b, strict=True)
        )
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k], tolerance) for k in a)
    return bool(a == b)


def rows_equal(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))


def sort_key(row: tuple[Any, ...]) -> str:
    """Stable ordering key for multiset comparison (numbers rounded to survive tolerance)."""
    parts: list[Any] = []
    for v in row:
        if isinstance(v, (Decimal, float)):
            parts.append(f"{float(v):.6g}")
        else:
            parts.append(repr(v))
    return "\x1f".join(str(p) for p in parts)
