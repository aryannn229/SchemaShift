"""Provenance groups, label template, per-class metrics, kappa, report rendering, external benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from schemashift.ai.mock_advisor import MockAdvisor
from schemashift.cli import app
from schemashift.metrics import agreement as agr
from schemashift.metrics import groups as grp
from schemashift.metrics import report
from schemashift.metrics.evaluate import (
    EquivalenceMetrics,
    evaluate_equivalence,
    load_corpus,
)
from schemashift.metrics.external import evaluate_external, external_names
from schemashift.metrics.labels import Provenance, label_template, provenance_of
from schemashift.pipeline import CompileOptions

SCHEMA = (
    "CREATE TABLE p (id INT PRIMARY KEY, email TEXT UNIQUE);"
    "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id) ON DELETE CASCADE);"
)


# ------------------------------------------------------------------------------ provenance
@pytest.mark.parametrize(
    ("by", "blind", "independent"),
    [
        ("rule-author", False, False),
        ("rule-author", True, False),
        ("Claude", True, False),
        ("", True, False),
        ("Dr Ada", False, False),  # someone else, but they saw the verdicts
        ("Dr Ada", True, True),
    ],
)
def test_only_a_blind_non_author_labeler_is_independent(
    by: str, blind: bool, independent: bool
) -> None:
    p = Provenance(labeled_by=by, labeled_blind=blind)
    assert p.independent is independent
    assert p.group == ("independent" if independent else "self_labeled")


def test_missing_fields_default_to_the_rule_author() -> None:
    p = provenance_of({})
    assert (p.labeled_by, p.labeled_blind, p.independent) == ("rule-author", False, False)


def test_every_bundled_corpus_label_file_declares_its_provenance() -> None:
    for case in load_corpus():
        assert "labeled_by" in case.expected and "labeled_blind" in case.expected, case.name


# --------------------------------------------------------------------------------- template
def _program(sql: str = SCHEMA) -> Any:
    from schemashift.ir import build_ir
    from schemashift.parser import parse
    from schemashift.semantic import analyze

    parsed = parse(sql)
    analysis = analyze(parsed.schema_, parsed.queries)
    return build_ir(analysis.graph, parsed.queries), sorted(analysis.graph.relationships)


def test_label_template_lists_guarantees_without_verdicts() -> None:
    program, rels = _program()
    text = label_template("demo", SCHEMA, program, rels, include_mechanical=False)
    doc = yaml.safe_load(text)
    assert doc["labeled_by"] == "" and doc["labeled_blind"] is True
    assert doc["placements"] == {"fk:c.p_id->p.id": "REFERENCE"}
    assert any(k.startswith("cascade_delete:") for k in doc["verdicts"])
    assert all(v["status"] == "" for v in doc["verdicts"].values())  # nothing pre-filled
    assert not any(k.startswith(("type:", "not_null:")) for k in doc["verdicts"])
    assert [b["match"] for b in doc["bulk"]] == ["type:*", "not_null:*"]
    for word in ("SAFE", "CHANGED", "BROKEN"):  # only the instructions mention statuses
        assert text.count(f'status: "{word}"') == 0


def test_label_template_can_include_mechanical_nodes() -> None:
    program, rels = _program()
    doc = yaml.safe_load(label_template("demo", SCHEMA, program, rels, include_mechanical=True))
    assert any(k.startswith("type:") for k in doc["verdicts"]) and "bulk" not in doc


def test_label_template_without_relationships() -> None:
    program, rels = _program("CREATE TABLE t (id INT PRIMARY KEY);")
    doc = yaml.safe_load(
        label_template("solo", "CREATE TABLE t (id INT PRIMARY KEY);", program, rels, False)
    )
    assert doc["placements"] == {}


def test_filled_template_is_scored_in_the_independent_group(tmp_path: Path) -> None:
    """Round trip: template -> label -> corpus -> independent metrics."""
    program, rels = _program()
    doc = yaml.safe_load(label_template("demo", SCHEMA, program, rels, include_mechanical=False))
    doc["labeled_by"] = "Dr Ada"
    for node_id, entry in doc["verdicts"].items():
        entry["status"] = "BROKEN" if node_id.startswith("cascade_delete:") else "SAFE"
    for b in doc["bulk"]:
        b["status"] = "SAFE"
    case = tmp_path / "demo"
    case.mkdir()
    (case / "schema.sql").write_text(SCHEMA, encoding="utf-8")
    (case / "expected.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    results = grp.evaluate_groups(grp.load_all_cases([tmp_path]))
    assert results["independent"].schemas == ["demo"] and results["self_labeled"].schemas == []
    assert results["independent"].metrics.total > 0
    assert results["independent"].labelers == ["Dr Ada"]


def test_duplicate_case_names_are_rejected(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / "same").mkdir(parents=True)
        (root / "same" / "schema.sql").write_text(SCHEMA, encoding="utf-8")
        (root / "same" / "expected.yaml").write_text(
            "verdicts: {}\nskip_coverage: true\n", encoding="utf-8"
        )
    with pytest.raises(ValueError, match="duplicate"):
        grp.load_all_cases([a, b])


# ---------------------------------------------------------------------------- precision/recall
def _metrics(confusion: dict[str, dict[str, int]]) -> EquivalenceMetrics:
    m = EquivalenceMetrics()
    for a, row in confusion.items():
        for p, n in row.items():
            m.confusion[a][p] = n  # type: ignore[index]
    return m


def test_class_and_problem_stats() -> None:
    m = _metrics(
        {
            "SAFE": {"SAFE": 8, "CHANGED": 2, "BROKEN": 0},
            "CHANGED": {"SAFE": 1, "CHANGED": 5, "BROKEN": 0},
            "BROKEN": {"SAFE": 0, "CHANGED": 1, "BROKEN": 3},
        }
    )
    c = grp.class_stats(m)
    assert c["SAFE"].precision == pytest.approx(8 / 9) and c["SAFE"].recall == pytest.approx(0.8)
    assert c["CHANGED"].precision == pytest.approx(5 / 8) and c["CHANGED"].recall == pytest.approx(
        5 / 6
    )
    assert c["BROKEN"].precision == 1.0 and c["BROKEN"].recall == pytest.approx(0.75)
    assert c["BROKEN"].f1 == pytest.approx(2 * 1.0 * 0.75 / 1.75)
    p = grp.problem_stats(m)  # flagged = CHANGED or BROKEN, severity aside
    assert p.precision == pytest.approx(9 / 11) and p.recall == pytest.approx(9 / 10)
    assert p.support == 10


def test_prf_edge_cases() -> None:
    empty = grp._prf(0, 0, 0)
    assert (empty.precision, empty.recall, empty.f1) == (None, None, None)
    assert grp._prf(0, 3, 0).recall is None and grp._prf(0, 3, 0).f1 is None
    assert grp._prf(0, 2, 2).f1 == 0.0
    assert grp.fmt(None) == "n/a" and grp.fmt(0.5) == "50.0%"


def test_groups_over_the_bundled_corpus_are_perfect_and_separate() -> None:
    results = grp.evaluate_groups()
    sl = results["self_labeled"]
    assert len(sl.schemas) >= 25 and sl.problem.recall == 1.0 and sl.problem.precision == 1.0
    assert results["independent"].schemas == []  # nothing is claimed as independent yet
    assert "no labeled schemas yet" in grp.format_group(results["independent"])
    text = grp.format_group(sl)
    assert "problem detection" in text and "confusion" in text
    j = grp.to_json(sl)
    assert j["failures"] == [] and json.dumps(j)


def test_metrics_would_catch_a_wrong_prediction() -> None:
    """A deliberately wrong label shows up as a failure and lowers the scores (sensitivity)."""
    cases = load_corpus()[:1]
    wrong = cases[0]
    first = next(iter(wrong.expected["verdicts"]))
    wrong.expected["verdicts"][first]["status"] = "BROKEN"
    metrics, _ = evaluate_equivalence(cases)
    assert metrics.mismatches and grp.problem_stats(metrics).precision is not None


# ----------------------------------------------------------------------------------- kappa
def test_kappa_values() -> None:
    assert agr.cohens_kappa(
        [True, False, True, False], [True, False, True, False]
    ) == pytest.approx(1.0)
    assert agr.cohens_kappa(
        [True, False, True, False], [False, True, False, True]
    ) == pytest.approx(-1.0)
    # 2 of 4 agree, chance agreement 0.5 -> kappa 0
    assert agr.cohens_kappa(
        [True, True, False, False], [True, False, True, False]
    ) == pytest.approx(0.0)
    assert agr.cohens_kappa([False, False, False], [False, False, False]) is None  # undefined
    assert agr.cohens_kappa([], []) is None and agr.cohens_kappa([True], [True, False]) is None


def test_mock_advisor_pairs_and_summary() -> None:
    pairs = agr.collect_pairs([("demo", SCHEMA, "", CompileOptions())], MockAdvisor())
    assert [p.relationship_id for p in pairs] == ["fk:c.p_id->p.id"]
    rep = agr.summarize(pairs, "mock", real_model=False)
    assert rep.pairs == 1 and rep.real_model is False and rep.rate in (0.0, 1.0)
    assert json.dumps(agr.to_json(rep))
    assert not any(p.agree for p in rep.disagreements)


def test_a_failing_advisor_is_skipped() -> None:
    class Broken:
        def suggest_placement(self, rel: Any, excerpt: str) -> Any:
            raise RuntimeError("down")

    assert agr.collect_pairs([("demo", SCHEMA, "", CompileOptions())], Broken()) == []  # type: ignore[arg-type]
    assert agr.summarize([], "x", False).rate is None


# ---------------------------------------------------------------------------------- external
def test_external_schemas_are_present_with_provenance() -> None:
    names = external_names()
    assert {"pagila", "chinook", "northwind", "supabase_slack_clone", "graphile_starter"} <= set(
        names
    )
    root = Path(__file__).resolve().parents[4] / "samples" / "external"
    for name in names:
        readme = (root / name / "README.md").read_text(encoding="utf-8")
        assert "**License:**" in readme and "**Source:**" in readme and "Upstream commit" in readme
        assert (root / name / "queries.sql").exists()


def test_external_evaluation_without_a_sandbox() -> None:
    data = evaluate_external()
    assert data["result_set"] == {"computed": False, "match": 0, "verified": 0, "rate": None}
    chinook, northwind = data["schemas"]["chinook"], data["schemas"]["northwind"]
    assert chinook["has_errors"] is False and northwind["has_errors"] is False
    assert chinook["queries_translated"] == chinook["queries"] == 16
    assert northwind["queries_translated"] == northwind["queries"]
    pagila = data["schemas"]["pagila"]
    assert pagila["has_errors"] and "UNSUPPORTED_TRIGGER" in pagila["diagnostics"]
    assert (
        "UNSUPPORTED_VIEW" in pagila["diagnostics"]
        and "UNSUPPORTED_PARTITIONING" in pagila["diagnostics"]
    )
    assert data["schemas"]["supabase_slack_clone"]["has_errors"]
    assert data["schemas"]["graphile_starter"]["diagnostics"]


# ------------------------------------------------------------------------------------ report
def _data() -> dict[str, Any]:
    results = grp.evaluate_groups()
    mock = agr.summarize(
        agr.collect_pairs([("demo", SCHEMA, "", CompileOptions())], MockAdvisor()), "mock", False
    )
    return {
        "date": "2026-01-01",
        "commit": "abc1234",
        "self_labeled": grp.to_json(results["self_labeled"]),
        "independent": grp.to_json(results["independent"]),
        "agreement": {"mock": agr.to_json(mock), "real": None},
        "external": evaluate_external(),
        "result_set": {"match": 21, "verified": 21},
    }


def test_markdown_keeps_groups_separate_and_honest() -> None:
    md = report.render_markdown(_data())
    assert "never merged into one headline number" in md
    assert "**pending**" in md  # the independent corpus is empty
    assert "How to add independent labels" in md
    assert "AI signal" in md and "Not measured yet" in md  # mutation + real model pending
    assert "| pagila |" in md and "not run" in md
    assert "None. Every labeled guarantee" in md


def test_markdown_with_independent_and_mutation_sections() -> None:
    d = _data()
    d["independent"] = dict(d["self_labeled"], schemas=["x"], labeled_by=["Dr Ada"])
    d["self_labeled"] = dict(
        d["self_labeled"],
        failures=[
            {
                "case": "c",
                "node": "n",
                "label": "SAFE",
                "predicted": "CHANGED",
                "label_why": "w",
                "checker_reason": "r",
            }
        ],
    )
    d["mutation"] = {
        "score": 0.8,
        "killed": 8,
        "total": 10,
        "survived": 2,
        "paths": ["equivalence/"],
        "command": "mutmut run",
    }
    md = report.render_markdown(d)
    assert "Mutation score 80.0%" in md and "`c` `n`: labeled SAFE" in md
    assert "**pending**" not in md


def test_compute_keeps_previous_sections_it_cannot_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prev = tmp_path / "metrics.json"
    prev.write_text(
        json.dumps(
            {
                "mutation": {"score": 0.5},
                "agreement": {"real": {"rate": 0.9}},
                "external": {
                    "result_set": {"match": 4, "verified": 5, "rate": 0.8, "computed": True},
                    "schemas": {},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(report, "JSON_PATH", prev)
    data = report.compute()
    assert data["mutation"] == {"score": 0.5}
    assert data["agreement"]["real"] == {"rate": 0.9}
    assert data["external"]["result_set"]["match"] == 4
    out = report.write_json(data, tmp_path / "out.json")
    assert json.loads(out.read_text(encoding="utf-8"))["self_labeled"]["labeled_nodes"] > 0


# ----------------------------------------------------------------------------------- CLI
def test_cli_label_template_to_stdout_and_to_disk(tmp_path: Path) -> None:
    schema = tmp_path / "schema.sql"
    schema.write_text(SCHEMA, encoding="utf-8")
    runner = CliRunner()
    res = runner.invoke(app, ["corpus", "label-template", str(schema)])
    assert res.exit_code == 0 and "labeled_blind: true" in res.output
    out = tmp_path / "out"
    res = runner.invoke(
        app, ["corpus", "label-template", str(schema), "--out", str(out), "--name", "demo"]
    )
    assert res.exit_code == 0 and "guarantees to label" in res.output
    assert (out / "demo" / "expected.yaml").exists() and (out / "demo" / "schema.sql").exists()


def test_cli_corpus_report_prints_both_groups() -> None:
    res = CliRunner().invoke(app, ["corpus", "report"])
    assert res.exit_code == 0
    assert "self_labeled:" in res.output and "independent: no labeled schemas yet" in res.output
    assert "optimizer vs mock advisor" in res.output and "not measured" in res.output
