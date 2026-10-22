"""Display-only SQL rendering for counterexamples (statements are never executed from this text).

The harness runs parameterized statements; a counterexample must be readable and replayable by a
person, so the bound parameters are inlined here as SQL literals.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from decimal import Decimal
from typing import Any

from schemashift.verification.ddl import q


def literal(value: Any) -> str:
    """A SQL literal for a Python value as it comes out of / goes into psycopg."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return "'" + value.isoformat() + "'"
    if isinstance(value, dt.timedelta):
        return f"'{value.total_seconds()} seconds'"
    if isinstance(value, uuid.UUID):
        return f"'{value}'"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(value).hex() + "'"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, default=str).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def render_sql(sql: str, params: tuple[Any, ...]) -> str:
    """Substitute ``%s`` placeholders with literals (display only)."""
    parts = re.split(r"(?<!%)%s", sql)
    if len(parts) - 1 != len(params):
        return sql  # do not guess: show the statement as it was
    out = [parts[0]]
    for value, tail in zip(params, parts[1:], strict=True):
        out.append(literal(value))
        out.append(tail)
    return "".join(out).replace("%%", "%")


def render_insert(table: str, row: dict[str, Any]) -> str:
    cols = list(row)
    return (
        f"INSERT INTO {q(table)} ({', '.join(q(c) for c in cols)}) "
        f"VALUES ({', '.join(literal(row[c]) for c in cols)});"
    )
