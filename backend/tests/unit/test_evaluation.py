"""Phase 27: the evaluation report renders every section and says when one was skipped."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from schemashift.evaluation import render, run_all, write_outputs

mod = importlib.import_module(
    "schemashift.evaluation.run_all"
)  # the package re-exports a function of that name

DATA = {
    "environment": {
        "date": "2026-10-23",
        "commit": "abc1234",
        "python": "3.13",
        "platform": "test-os",
        "cpu_count": 8,
        "memory_gb": 16.0,
        "postgres": "PostgreSQL 16",
        "mongodb": "7.0",
        "versions": {"z3": "4.x"},
        "seeds": {"fuzz": 52},
    },
    "corpus": {
        "equivalence": {
            "corpus_schemas": 32,
            "labeled_nodes": 883,
            "detection_rate": 1.0,
            "false_positive_rate": 0.0,
            "severity_accuracy": 1.0,
        },
        "independent": {"schemas": []},
        "agreement": {"mock": {"kappa": 0.4}},
    },
    "evidence": {
        "computed": True,
        "schemas": 41,
        "without_smt": {"PROVEN": 1},
        "with_smt": {"PROVEN": 2},
        "smt_table_outcomes": {"EQUIVALENT": 3},
    },
    "smt_soundness": {
        "computed": True,
        "examples": 10,
        "seconds": 1.0,
        "passed": True,
        "stats": {"proofs": 5},
    },
    "fuzz": {
        "computed": True,
        "cases": 5,
        "seed": 52,
        "queries": 9,
        "relation_checks": 4,
        "optimizer_checked": 1,
        "failures": 0,
        "seconds": 2.0,
        "bugs_found_to_date": {"fixed": 14, "open": 0},
    },
    "verification": {"computed": False, "skipped": "needs PostgreSQL and MongoDB"},
    "bench": {"computed": False, "skipped": "needs PostgreSQL and MongoDB"},
    "mutation": {"score": 0.89, "killed": 1, "total": 2},
}


def test_render_covers_every_section_and_flags_skipped_ones() -> None:
    text = render(DATA)
    for heading in (
        "Environment",
        "Checker accuracy",
        "Evidence levels",
        "Z3 soundness",
        "fuzzing",
        "Result-set correctness",
        "Benchmarks",
        "Mutation score",
    ):
        assert heading in text
    assert "self-labeled" in text.lower() and "Independent (blind" in text
    assert (
        "Skipped: needs PostgreSQL and MongoDB" in text
    )  # no number invented for a skipped section
    assert "seeds: fuzz=52".lower() in text.lower()


def test_write_outputs_replaces_the_generated_block_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "EVALUATION.md").write_text("# Methodology\nkept text\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"date": "x"}), encoding="utf-8")
    monkeypatch.setattr(mod, "DOCS", tmp_path)
    write_outputs(DATA)
    first = (tmp_path / "EVALUATION.md").read_text(encoding="utf-8")
    assert first.startswith("# Methodology\nkept text") and mod.BEGIN in first and mod.END in first
    write_outputs({**DATA, "environment": {**DATA["environment"], "commit": "def5678"}})
    second = (tmp_path / "EVALUATION.md").read_text(encoding="utf-8")
    assert second.count(mod.BEGIN) == 1 and "def5678" in second and "abc1234" not in second
    assert second.startswith("# Methodology\nkept text")
    merged = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert merged["date"] == "x" and merged["evaluation"]["fuzz"]["failures"] == 0
    assert (
        json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))["environment"][
            "commit"
        ]
        == "def5678"
    )


def test_run_all_without_databases_skips_those_sections_honestly() -> None:
    data = run_all(None, skip=frozenset({"corpus", "evidence", "mutation"}), log=lambda _: None)
    for name in ("smt_soundness", "fuzz", "verification", "bench"):
        assert data[name]["computed"] is False and "PostgreSQL" in data[name]["skipped"]
    assert data["corpus"]["skipped"] == "skipped by --skip"
    assert data["environment"]["seeds"]["fuzz"] == 52


def test_a_failing_section_does_not_hide_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> dict[str, object]:
        raise RuntimeError("no luck")

    monkeypatch.setattr(mod, "evidence_section", boom)
    data = run_all(None, skip=frozenset({"corpus", "mutation"}), log=lambda _: None)
    assert (
        data["evidence"]["computed"] is False
        and "RuntimeError: no luck" in data["evidence"]["skipped"]
    )
    assert "smt_soundness" in data
