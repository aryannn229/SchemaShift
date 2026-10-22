"""The policy file (``schemashift.policy.yaml``): which regressions are unacceptable."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from schemashift.models.base import FrozenModel

Severity = Literal["error", "warning", "off"]
CONSTRAINTS = ("unique", "primary_key", "foreign_key", "not_null", "check")


class Policy(FrozenModel):
    """Defaults: strict about guarantees going SAFE to BROKEN and about dropped constraints."""

    version: int = 1
    # transitions of a guarantee verdict that fail the check / only warn, written "SAFE->BROKEN"
    fail_on_regression: tuple[str, ...] = ("SAFE->BROKEN", "CHANGED->BROKEN")
    warn_on_regression: tuple[str, ...] = ("SAFE->CHANGED",)
    # constraints whose removal fails the check (the others warn)
    fail_on_removed: tuple[str, ...] = CONSTRAINTS
    max_risk_increase: int | None = 10  # risk score points; None = unlimited
    max_risk_score: int | None = None
    irreversible_steps: Severity = "warning"
    overall_verdict_at_least: Literal["CHANGED", "BROKEN", "never"] = "never"
    paths: tuple[str, ...] = Field(default=("**/*.sql",))  # files the GitHub Action checks

    @classmethod
    def load(cls, path: Path | None) -> Policy:
        if path is None or not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        flat: dict[str, object] = {}
        rules: dict[str, Any] = data.get("rules", data) or {}
        for key, value in rules.items():
            if key == "guarantee_regression" and isinstance(value, dict):
                flat["fail_on_regression"] = tuple(value.get("fail_on", cls().fail_on_regression))
                flat["warn_on_regression"] = tuple(value.get("warn_on", cls().warn_on_regression))
            elif key == "removed_constraints" and isinstance(value, dict):
                flat["fail_on_removed"] = tuple(value.get("fail_on", cls().fail_on_removed))
            elif key == "risk" and isinstance(value, dict):
                flat["max_risk_increase"] = value.get("max_increase", cls().max_risk_increase)
                flat["max_risk_score"] = value.get("max_score")
            elif key == "irreversible_steps":
                flat["irreversible_steps"] = {"fail": "error", "warn": "warning"}.get(value, value)
            elif key == "overall_verdict":
                flat["overall_verdict_at_least"] = value
            elif key in cls.model_fields:
                flat[key] = tuple(value) if isinstance(value, list) else value
        if "paths" in data:
            flat["paths"] = tuple(data["paths"])
        return cls.model_validate(flat)
