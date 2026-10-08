"""Source locations and diagnostics shared by all stages."""

from typing import Literal

from schemashift.models.base import FrozenModel


class SourceSpan(FrozenModel):
    """1-based line/column range. ``col_end`` is exclusive."""

    line_start: int
    col_start: int
    line_end: int
    col_end: int


Severity = Literal["error", "warning", "info"]


class Diagnostic(FrozenModel):
    severity: Severity
    code: str
    message: str
    location: SourceSpan | None = None
