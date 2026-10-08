"""BSON value wrappers and renderers (mongosh JavaScript, Python/pymongo).

Pipelines and documents are built as plain Python structures. Values that have no JSON form are
wrapped in tiny frozen dataclasses so each emitter can print them natively, and
``materialize`` turns them into real driver objects for the verification harness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
WIDTH = 96


@dataclass(frozen=True)
class DateValue:
    iso: str  # ISO-8601, UTC


@dataclass(frozen=True)
class DecimalValue:
    text: str


@dataclass(frozen=True)
class RegexValue:
    pattern: str
    flags: str = ""


@dataclass(frozen=True)
class UuidValue:
    text: str


@dataclass(frozen=True)
class NowValue:
    """The current time (``new Date()`` / ``datetime.now(UTC)``)."""


@dataclass(frozen=True)
class RawJs:
    """Escape hatch: already-rendered code, per language."""

    js: str
    py: str


# ----------------------------------------------------------------------------- JS
def js_string(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _js_key(key: str) -> str:
    return key if _IDENT.match(key) else js_string(key)


def to_js(value: Any, indent: int = 0) -> str:
    """Render ``value`` as mongosh JavaScript."""
    flat = _js_flat(value)
    if len(flat) + indent <= WIDTH or not isinstance(value, (dict, list, tuple)):
        return flat
    pad, inner = "  " * indent, "  " * (indent + 1)
    if isinstance(value, dict):
        rows = [f"{inner}{_js_key(str(k))}: {to_js(v, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(rows) + f"\n{pad}}}"
    rows = [f"{inner}{to_js(v, indent + 1)}" for v in value]
    return "[\n" + ",\n".join(rows) + f"\n{pad}]"


def _js_flat(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, Decimal):
        return f'NumberDecimal("{value}")'
    if isinstance(value, str):
        return js_string(value)
    if isinstance(value, DateValue):
        return f"new Date({js_string(value.iso)})"
    if isinstance(value, NowValue):
        return "new Date()"
    if isinstance(value, DecimalValue):
        return f"NumberDecimal({js_string(value.text)})"
    if isinstance(value, UuidValue):
        return f"UUID({js_string(value.text)})"
    if isinstance(value, RegexValue):
        body = value.pattern.replace("/", "\\/")
        return f"/{body}/{value.flags}"
    if isinstance(value, RawJs):
        return value.js
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_js_key(str(k))}: {_js_flat(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_js_flat(v) for v in value) + "]"
    raise TypeError(f"cannot render {type(value).__name__} as JavaScript")


# ------------------------------------------------------------------------- Python
def to_py(value: Any, indent: int = 0) -> str:
    """Render ``value`` as Python (pymongo) source."""
    flat = _py_flat(value)
    if len(flat) + indent <= WIDTH or not isinstance(value, (dict, list, tuple)):
        return flat
    pad, inner = "    " * indent, "    " * (indent + 1)
    if isinstance(value, dict):
        rows = [f"{inner}{_py_flat(str(k))}: {to_py(v, indent + 1)}" for k, v in value.items()]
        return "{\n" + ",\n".join(rows) + f",\n{pad}}}"
    rows = [f"{inner}{to_py(v, indent + 1)}" for v in value]
    return "[\n" + ",\n".join(rows) + f",\n{pad}]"


def _py_flat(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, Decimal):
        return f'Decimal128("{value}")'
    if isinstance(value, DateValue):
        return f"datetime.fromisoformat({value.iso!r})"
    if isinstance(value, NowValue):
        return "datetime.now(timezone.utc)"
    if isinstance(value, DecimalValue):
        return f"Decimal128({value.text!r})"
    if isinstance(value, UuidValue):
        return f"uuid.UUID({value.text!r})"
    if isinstance(value, RegexValue):
        return f"Regex({value.pattern!r}, {value.flags!r})"
    if isinstance(value, RawJs):
        return value.py
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py_flat(str(k))}: {_py_flat(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_py_flat(v) for v in value) + "]"
    raise TypeError(f"cannot render {type(value).__name__} as Python")


# ------------------------------------------------------------------- materialize
def materialize(value: Any) -> Any:
    """Convert wrappers into real driver objects (for running pipelines with pymongo)."""
    import uuid

    from bson import Binary, Decimal128
    from bson.regex import Regex

    if isinstance(value, DateValue):
        return datetime.fromisoformat(value.iso)
    if isinstance(value, NowValue):
        return datetime.now(UTC)
    if isinstance(value, DecimalValue):
        return Decimal128(value.text)
    if isinstance(value, Decimal):
        return Decimal128(value)
    if isinstance(value, UuidValue):
        return Binary.from_uuid(uuid.UUID(value.text))
    if isinstance(value, RegexValue):
        return Regex(value.pattern, value.flags)
    if isinstance(value, dict):
        return {k: materialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [materialize(v) for v in value]
    return value
