"""Phase 25: policy checks, SARIF 2.1.0 output, the git baseline and the GitHub Action files."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from schemashift.ci import Policy, check_schemas, to_json, to_markdown, to_sarif, to_text
from schemashift.cli import app

ROOT = Path(__file__).resolve().parents[4]
OLD = (
    "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, age INT);\n"
    "CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL "
    "REFERENCES users(id) ON DELETE CASCADE);\n"
)
NO_UNIQUE = OLD.replace(" UNIQUE", "")
NO_FK = OLD.replace(" REFERENCES users(id) ON DELETE CASCADE", "")


def run(old: str, new: str, policy: Policy | None = None) -> Any:
    return check_schemas(old, new, policy=policy, file="db/schema.sql")[0]


def test_an_unchanged_schema_passes_with_no_violations() -> None:
    r = run(OLD, OLD)
    assert r.passed and r.violations == () and r.changes == 0 and r.risk_before == r.risk_after


def test_a_removed_unique_fails_and_names_table_rule_and_verdict() -> None:
    r = run(OLD, NO_UNIQUE)
    assert not r.passed
    v = next(x for x in r.violations if x.policy_rule == "SS-REMOVED-CONSTRAINT")
    assert v.level == "error" and v.table == "users" and v.equivalence_rule == "EQ-VALUE-UNIQ"
    assert v.message.startswith("UNIQUE removed on users.email") and "ValueUniqueness" in v.message
    assert "rule EQ-VALUE-UNIQ" in v.message and v.node_id == "unique:users.email"


def test_policy_can_downgrade_a_removal_to_a_warning() -> None:
    r = run(OLD, NO_UNIQUE, Policy(fail_on_removed=("foreign_key",)))
    assert r.passed and any(v.level == "warning" for v in r.violations)


def test_a_dropped_foreign_key_is_reported() -> None:
    r = run(OLD, NO_FK)
    msgs = " | ".join(v.message for v in r.violations)
    assert "FOREIGN KEY removed on orders" in msgs and not r.passed


def test_risk_limits() -> None:
    worse = "CREATE TABLE users (id INT PRIMARY KEY);" + "".join(
        f"CREATE TABLE t{i} (a INT, b TEXT);" for i in range(6)
    )
    r = run("CREATE TABLE users (id INT PRIMARY KEY);", worse, Policy(max_risk_increase=1))
    assert any(v.policy_rule == "SS-RISK" for v in r.violations) and not r.passed
    r2 = run(OLD, OLD, Policy(max_risk_score=1))
    assert any("exceeds the limit" in v.message for v in r2.violations)


def test_irreversible_steps_follow_the_policy_severity() -> None:
    new = (
        "CREATE TABLE users (id SERIAL, email TEXT NOT NULL UNIQUE, age INT, "
        "PRIMARY KEY (email));\n" + OLD.split("\n")[1]
    )
    assert any(
        v.policy_rule == "SS-IRREVERSIBLE" and v.level == "warning"
        for v in run(OLD, new).violations
    )
    assert not run(OLD, new, Policy(irreversible_steps="error")).passed
    off = run(OLD, new, Policy(irreversible_steps="off"))
    assert not any(v.policy_rule == "SS-IRREVERSIBLE" for v in off.violations)


def test_policy_file_round_trip(tmp_path: Path) -> None:
    f = tmp_path / "schemashift.policy.yaml"
    f.write_text(
        "rules:\n"
        "  guarantee_regression:\n    fail_on: ['SAFE->CHANGED']\n"
        "  removed_constraints:\n    fail_on: [unique]\n"
        "  risk:\n    max_increase: 3\n    max_score: 90\n"
        "  irreversible_steps: fail\n  overall_verdict: BROKEN\n",
        encoding="utf-8",
    )
    p = Policy.load(f)
    assert p.fail_on_regression == ("SAFE->CHANGED",) and p.fail_on_removed == ("unique",)
    assert (p.max_risk_increase, p.max_risk_score, p.irreversible_steps) == (3, 90, "error")
    assert p.overall_verdict_at_least == "BROKEN"
    assert Policy.load(tmp_path / "missing.yaml") == Policy()
    example = Policy.load(ROOT / "docs" / "examples" / "schemashift.policy.yaml")
    assert example == Policy()  # the documented example equals the defaults


# The parts of the SARIF 2.1.0 schema that GitHub code scanning requires.
SARIF_SUBSET: dict[str, Any] = {
    "type": "object",
    "required": ["version", "runs"],
    "properties": {
        "version": {"const": "2.1.0"},
        "runs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["tool", "results"],
                "properties": {
                    "tool": {
                        "type": "object",
                        "required": ["driver"],
                        "properties": {
                            "driver": {
                                "type": "object",
                                "required": ["name", "rules"],
                                "properties": {
                                    "rules": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["id", "shortDescription"],
                                        },
                                    }
                                },
                            }
                        },
                    },
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["ruleId", "level", "message", "locations"],
                            "properties": {
                                "level": {"enum": ["error", "warning", "note", "none"]},
                                "message": {"type": "object", "required": ["text"]},
                                "locations": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["physicalLocation"],
                                        "properties": {
                                            "physicalLocation": {
                                                "type": "object",
                                                "required": ["artifactLocation", "region"],
                                                "properties": {
                                                    "region": {
                                                        "type": "object",
                                                        "required": ["startLine"],
                                                        "properties": {"startLine": {"minimum": 1}},
                                                    }
                                                },
                                            }
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def test_sarif_is_valid_and_has_source_regions() -> None:
    sarif = to_sarif([run(OLD, NO_UNIQUE), run(OLD, NO_FK)])
    jsonschema.validate(sarif, SARIF_SUBSET)
    results = sarif["runs"][0]["results"]
    assert results and all(r["ruleId"].startswith("SS-") for r in results)
    rule_ids = [r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]]
    assert all(rule_ids[r["ruleIndex"]] == r["ruleId"] for r in results)
    regions = [r["locations"][0]["physicalLocation"]["region"]["startLine"] for r in results]
    assert any(line > 1 for line in regions) or any(line == 2 for line in regions)
    uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in results}
    assert uris == {"db/schema.sql"}
    assert any(r["properties"]["equivalenceRule"] == "EQ-VALUE-UNIQ" for r in results)


def test_other_formats() -> None:
    reports = [run(OLD, NO_UNIQUE)]
    assert "FAIL  db/schema.sql" in to_text(reports) and "SS-REMOVED-CONSTRAINT" in to_text(reports)
    data = json.loads(to_json(reports))
    assert data["passed"] is False and data["reports"][0]["violations"]
    md = to_markdown(reports)
    assert md.startswith("<!-- schemashift-check -->") and "UNIQUE removed on users.email" in md
    assert "EQ-VALUE-UNIQ" in md and "changes need attention" in md
    assert "no policy violations" in to_markdown([run(OLD, OLD)])


def _git(cwd: Path, *args: str) -> None:
    cmd = ["git", "-C", str(cwd), "-c", "user.email=a@b.c", "-c", "user.name=t", *args]
    subprocess.run(cmd, check=True, capture_output=True)


def test_cli_compares_against_a_git_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "db").mkdir()
    sql = tmp_path / "db" / "schema.sql"
    sql.write_text(OLD, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    sql.write_text(NO_UNIQUE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    r = runner.invoke(app, ["check", "db/schema.sql", "--baseline-ref", "HEAD"])
    assert r.exit_code == 1 and "UNIQUE removed on users.email" in r.output
    out = tmp_path / "out.sarif"
    args = ["check", "db/schema.sql", "--baseline-ref", "HEAD", "--format", "sarif", "-o", str(out)]
    assert runner.invoke(app, [*args, "--no-fail"]).exit_code == 0
    jsonschema.validate(json.loads(out.read_text(encoding="utf-8")), SARIF_SUBSET)
    (tmp_path / "db" / "new.sql").write_text(OLD, encoding="utf-8")  # a new file has no baseline
    assert runner.invoke(app, ["check", "db/new.sql", "--baseline-ref", "HEAD"]).exit_code == 0
    assert runner.invoke(app, ["check", "db/schema.sql"]).exit_code == 2
    bad = ["check", "db/schema.sql", "--baseline", "db/schema.sql", "--format", "xml"]
    assert runner.invoke(app, bad).exit_code == 2


def test_the_github_action_and_sample_workflow_are_well_formed() -> None:
    action_file = ROOT / ".github/actions/schemashift-check/action.yml"
    action = yaml.safe_load(action_file.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert {"policy", "paths", "baseline-ref"} <= set(action["inputs"])
    uses = [s.get("uses", "") for s in action["runs"]["steps"]]
    assert any(u.startswith("github/codeql-action/upload-sarif") for u in uses)
    assert any(u.startswith("actions/github-script") for u in uses)
    commands = "\n".join(s.get("run", "") for s in action["runs"]["steps"])
    assert "schemashift check" in commands and "--format sarif" in commands
    assert "--format markdown" in commands
    wf = yaml.safe_load((ROOT / "docs/examples/schemashift-pr.yml").read_text(encoding="utf-8"))
    assert wf[True]["pull_request"]["paths"]  # the key `on` parses as True in YAML 1.1
    assert wf["permissions"]["security-events"] == "write"
    assert wf["permissions"]["pull-requests"] == "write"
    steps = wf["jobs"]["check"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0 and "schemashift-check" in steps[1]["uses"]
