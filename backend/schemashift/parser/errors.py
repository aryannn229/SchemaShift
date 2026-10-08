"""Parser diagnostics and codes."""

from __future__ import annotations

from schemashift.models.source import Diagnostic, SourceSpan

SYNTAX_ERROR = "PARSE001"
UNRECOGNIZED = "PARSE002"
IGNORED = "PARSE003"
TXN_STRUCTURE = "PARSE004"
DUPLICATE_PK = "PARSE005"
UNKNOWN_TABLE = "PARSE006"
UNSUPPORTED_TYPE = "PARSE007"
UNKNOWN_TYPE = "PARSE008"
DUPLICATE_OBJECT = "PARSE009"

# UnsupportedConstruct diagnostics (code is UNSUPPORTED_<NAME>)
UNSUPPORTED_NAMES = (
    "TRIGGER",
    "FUNCTION",
    "VIEW",
    "CTE",
    "WINDOW_FUNCTION",
    "SUBQUERY",
    "OUTER_JOIN",
    "INHERITANCE",
    "PARTITIONING",
    "EXCLUSION_CONSTRAINT",
    "LATERAL",
    "STATEMENT",
    "ALTER_ACTION",
    "JOIN",
    "FUNCTION_CALL",
    "SET_OPERATION",
    "EXPRESSION_INDEX",
    "CREATE_TABLE_AS",
    "ROLLBACK",
    "QUERY_FEATURE",
)


class ParseError(Exception):
    """Raised inside the parser; converted into a ``Diagnostic`` at statement level."""

    def __init__(self, code: str, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.span = span

    def to_diagnostic(self) -> Diagnostic:
        return Diagnostic(
            severity="error", code=self.code, message=self.message, location=self.span
        )


def unsupported(name: str, message: str, span: SourceSpan | None = None) -> ParseError:
    assert name in UNSUPPORTED_NAMES, name
    return ParseError(f"UNSUPPORTED_{name}", f"Unsupported construct: {message}", span)
