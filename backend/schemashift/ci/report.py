"""Output formats of `schemashift check`: text, JSON, SARIF 2.1.0 and a Markdown PR summary."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from schemashift import __version__
from schemashift.ci.check import POLICY_RULES, CheckReport, Violation

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFO_URI = "https://github.com/aryannn229/SchemaShift/blob/main/docs/CI.md"


def to_text(reports: Sequence[CheckReport]) -> str:
    lines: list[str] = []
    for r in reports:
        status = "PASS" if r.passed else "FAIL"
        lines.append(
            f"{status}  {r.file}: {r.changes} change(s), "
            f"verdict {r.overall_before} -> {r.overall_after}, "
            f"risk {r.risk_before} -> {r.risk_after}"
        )
        for v in r.violations:
            where = f"{v.location.line_start}:{v.location.col_start}" if v.location else "-"
            lines.append(f"  {v.level.upper():7} {v.policy_rule:22} {where:>7}  {v.message}")
    return "\n".join(lines)


def to_json(reports: Sequence[CheckReport]) -> str:
    return json.dumps(
        {
            "passed": all(r.passed for r in reports),
            "reports": [r.model_dump(mode="json") for r in reports],
        },
        indent=2,
    )


def _region(v: Violation) -> dict[str, int]:
    if v.location is None:
        return {"startLine": 1, "startColumn": 1}
    s = v.location
    return {
        "startLine": max(1, s.line_start),
        "startColumn": max(1, s.col_start),
        "endLine": max(1, s.line_end),
        "endColumn": max(1, s.col_end),
    }


def to_sarif(reports: Sequence[CheckReport]) -> dict[str, Any]:
    """SARIF 2.1.0 with source regions, ready for GitHub code scanning."""
    rule_ids = list(POLICY_RULES)
    results: list[dict[str, Any]] = []
    for r in reports:
        for v in r.violations:
            uri = r.file if v.file == "new" else r.baseline_file
            results.append(
                {
                    "ruleId": v.policy_rule,
                    "ruleIndex": rule_ids.index(v.policy_rule),
                    "level": v.level,
                    "message": {"text": v.message},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": uri.replace("\\", "/")},
                                "region": _region(v),
                            }
                        }
                    ],
                    "partialFingerprints": {
                        "schemashift/v1": (
                            f"{v.policy_rule}:{v.equivalence_rule}:{v.node_id}:{v.table}"
                        )
                    },
                    "properties": {
                        "equivalenceRule": v.equivalence_rule,
                        "guarantee": v.node_id,
                        "table": v.table,
                        "from": v.old,
                        "to": v.new,
                    },
                }
            )
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "SchemaShift",
                        "version": __version__,
                        "informationUri": INFO_URI,
                        "rules": [
                            {
                                "id": rid,
                                "name": rid.title().replace("-", ""),
                                "shortDescription": {"text": text},
                                "helpUri": INFO_URI,
                                "defaultConfiguration": {"level": "error"},
                            }
                            for rid, text in POLICY_RULES.items()
                        ],
                    }
                },
                "results": results,
            }
        ],
    }


def to_markdown(reports: Sequence[CheckReport], marker: str = "<!-- schemashift-check -->") -> str:
    """The pull request comment: one line per violation (rule, table, verdict change)."""
    failed = [r for r in reports if not r.passed]
    head = "### SchemaShift: " + ("changes need attention" if failed else "no policy violations")
    lines = [marker, head, ""]
    for r in reports:
        lines.append(
            f"**{r.file}**: {r.changes} schema change(s); verdict {r.overall_before} to "
            f"{r.overall_after}; migration risk {r.risk_before} to {r.risk_after}"
        )
        if not r.violations:
            lines.append("- no violations")
        for v in r.violations:
            icon = "FAIL" if v.level == "error" else "warn"
            where = f" (line {v.location.line_start})" if v.location else ""
            lines.append(f"- **{icon}** `{v.policy_rule}`{where}: {v.message}")
        lines.append("")
    lines.append(
        "Policy: `schemashift.policy.yaml`. Rules and evidence: https://github.com/aryannn229/SchemaShift/blob/main/docs/CI.md"
    )
    return "\n".join(lines)
