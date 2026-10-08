"""Semantic diagnostics (re-exports the shared model plus code constants)."""

from schemashift.models.source import Diagnostic, Severity, SourceSpan

SEM001 = "SEM001"  # FK references a table that does not exist
SEM002 = "SEM002"  # FK references columns that do not exist
SEM003 = "SEM003"  # FK column count != referenced column count
SEM004 = "SEM004"  # FK type mismatch
SEM005 = "SEM005"  # FK target is not PK/UNIQUE in parent
SEM006 = "SEM006"  # duplicate table / column
SEM007 = "SEM007"  # PK/UNIQUE/INDEX references unknown column
SEM008 = "SEM008"  # table has no primary key
SEM009 = "SEM009"  # circular FK chain
SEM010 = "SEM010"  # ON DELETE SET NULL on NOT NULL column
SEM011 = "SEM011"  # self-referencing FK
SEM012 = "SEM012"  # query references unknown table/column

__all__ = ["Diagnostic", "Severity", "SourceSpan"]


def diag(
    severity: Severity, code: str, message: str, location: SourceSpan | None = None
) -> Diagnostic:
    return Diagnostic(severity=severity, code=code, message=message, location=location)
