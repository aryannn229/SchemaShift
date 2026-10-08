"""Statement classification and low-level sqlglot parsing with position mapping."""

from __future__ import annotations

import logging
import re
from typing import Literal

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp

from schemashift.models.source import SourceSpan
from schemashift.parser import errors
from schemashift.parser.errors import ParseError, unsupported
from schemashift.parser.splitter import RawStatement, SourceMap

logging.getLogger("sqlglot").setLevel(logging.ERROR)

StmtClass = Literal[
    "create_table",
    "create_index",
    "create_type",
    "alter_table",
    "select",
    "insert",
    "update",
    "delete",
    "txn_begin",
    "txn_end",
    "txn_rollback",
]

DDL_CLASSES: frozenset[str] = frozenset(
    {"create_table", "create_index", "create_type", "alter_table"}
)
TXN_CLASSES: frozenset[str] = frozenset({"txn_begin", "txn_end", "txn_rollback"})

_CREATE_RE = re.compile(
    r"^CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:(?:TEMP|TEMPORARY|UNLOGGED|GLOBAL|LOCAL|MATERIALIZED|RECURSIVE|CONSTRAINT|UNIQUE)\s+)*"
    r"(\w+)",
    re.IGNORECASE,
)
_UNSUPPORTED_CREATE: dict[str, tuple[str, str]] = {
    "TRIGGER": ("TRIGGER", "triggers"),
    "FUNCTION": ("FUNCTION", "stored functions"),
    "PROCEDURE": ("FUNCTION", "stored procedures"),
    "VIEW": ("VIEW", "views"),
}
_SIMPLE_WORDS: dict[str, StmtClass] = {
    "SELECT": "select",
    "INSERT": "insert",
    "UPDATE": "update",
    "DELETE": "delete",
    "BEGIN": "txn_begin",
    "START": "txn_begin",
    "COMMIT": "txn_end",
    "END": "txn_end",
    "ROLLBACK": "txn_rollback",
    "ABORT": "txn_rollback",
}
_OTHER_STATEMENTS = frozenset(
    {
        "DO",
        "COPY",
        "GRANT",
        "REVOKE",
        "DROP",
        "TRUNCATE",
        "COMMENT",
        "SET",
        "RESET",
        "VACUUM",
        "ANALYZE",
        "EXPLAIN",
        "LOCK",
        "CALL",
        "EXECUTE",
        "LISTEN",
        "NOTIFY",
        "SAVEPOINT",
        "RELEASE",
        "REINDEX",
        "CLUSTER",
    }
)
_ALTER_TABLE_RE = re.compile(r"^ALTER\s+TABLE\b", re.IGNORECASE)
_DEFER_NOISE = re.compile(
    r"\bNOT\s+DEFERRABLE\b|\bINITIALLY\s+(?:DEFERRED|IMMEDIATE)\b|\bNOT\s+VALID\b", re.IGNORECASE
)


_TOKEN_REPR = re.compile(r"<Token token_type: [^,]*, text: (.*?), line: \d+, col: .*?>")


def _clean_message(message: str) -> str:
    return _TOKEN_REPR.sub(lambda m: repr(m.group(1)), message).splitlines()[0]


def classify(raw: RawStatement, span: SourceSpan) -> StmtClass:
    """Decide what kind of statement this is, or raise a ParseError."""
    masked = raw.masked
    m = re.match(r"\w+", masked)
    if not m:
        raise ParseError(errors.UNRECOGNIZED, "Unrecognized statement", span)
    word = m.group(0).upper()

    if word == "CREATE":
        cm = _CREATE_RE.match(masked)
        obj = cm.group(1).upper() if cm else ""
        if obj == "TABLE":
            return "create_table"
        if obj == "INDEX":
            return "create_index"
        if obj == "TYPE":
            return "create_type"
        if obj in _UNSUPPORTED_CREATE:
            name, what = _UNSUPPORTED_CREATE[obj]
            raise unsupported(name, f"{what} are not supported", span)
        raise unsupported("STATEMENT", f"CREATE {obj or '?'} is not supported", span)
    if word == "ALTER":
        if _ALTER_TABLE_RE.match(masked):
            return "alter_table"
        raise unsupported("STATEMENT", "only ALTER TABLE ... ADD CONSTRAINT is supported", span)
    if word == "WITH":
        raise unsupported("CTE", "common table expressions (WITH) are not supported", span)
    if word in _SIMPLE_WORDS:
        return _SIMPLE_WORDS[word]
    if word in _OTHER_STATEMENTS:
        raise unsupported("STATEMENT", f"{word} statements are not supported", span)
    raise ParseError(errors.UNRECOGNIZED, f"Unrecognized statement starting with {word}", span)


def neutralize(raw: RawStatement) -> str:
    """Blank constructs sqlglot cannot parse but we do not need (offset preserving)."""
    text = raw.text
    for m in _DEFER_NOISE.finditer(raw.masked):
        text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end() :]
    return text


def parse_sql(
    parse_text: str,
    raw: RawStatement,
    smap: SourceMap,
    stmt_span: SourceSpan,
    *,
    positions_valid: bool = True,
) -> exp.Expr:
    """Parse one statement with sqlglot, mapping failures to positioned ParseErrors."""
    try:
        ast = sqlglot.parse_one(parse_text, dialect="postgres")
    except sqlglot_errors.ParseError as exc:
        span = stmt_span
        if positions_valid and exc.errors:
            info = exc.errors[0]
            line = info.get("line")
            col = info.get("col")
            highlight = str(info.get("highlight") or "")
            if isinstance(line, int) and isinstance(col, int):
                s_line, s_col = smap.position(raw.start)
                start_col = max(1, col - len(highlight) + 1)
                abs_line = s_line + line - 1
                abs_col = start_col + (s_col - 1 if line == 1 else 0)
                span = SourceSpan(
                    line_start=abs_line,
                    col_start=abs_col,
                    line_end=abs_line,
                    col_end=abs_col + max(1, len(highlight)),
                )
        first = _clean_message(str(exc.errors[0].get("description")) if exc.errors else str(exc))
        raise ParseError(errors.SYNTAX_ERROR, f"Syntax error: {first}", span) from exc
    except sqlglot_errors.TokenError as exc:
        raise ParseError(errors.SYNTAX_ERROR, f"Syntax error: {exc}", stmt_span) from exc
    if ast is None or isinstance(ast, exp.Command):
        raise ParseError(
            errors.SYNTAX_ERROR, "Syntax error: statement could not be parsed", stmt_span
        )
    return ast
