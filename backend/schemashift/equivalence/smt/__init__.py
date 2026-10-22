"""SMT-backed constraint reasoning (Phase 26).

Does the generated validator accept exactly what the SQL constraints accept? Z3 decides it for a
documented
fragment (SPEC.md section 27); anything else is UNVERIFIED, never SAFE.

``run_smt`` / ``apply_smt`` are imported lazily: the checker needs the code generator, which needs
the
pipeline, which imports the result types of this package.
"""

from __future__ import annotations

from typing import Any

from schemashift.equivalence.smt.model import SmtFinding, SmtReport, TableResult
from schemashift.equivalence.smt.sql import Unsupported

__all__ = ["SmtFinding", "SmtReport", "TableResult", "Unsupported", "apply_smt", "run_smt"]


def __getattr__(name: str) -> Any:
    if name in ("run_smt", "apply_smt"):
        from schemashift.equivalence.smt import check

        return getattr(check, name)
    raise AttributeError(name)
