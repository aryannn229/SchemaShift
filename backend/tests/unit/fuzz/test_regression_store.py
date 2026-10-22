"""Saving and loading minimal reproducers (no databases needed)."""

from __future__ import annotations

from pathlib import Path

import yaml

from schemashift.fuzz.regression import DEFAULT_DIR, entry_for, load_all, save
from schemashift.fuzz.relations import Finding
from schemashift.fuzz.spec import ColSpec, FuzzCase, SchemaSpec, SelectSpec, TableSpec


def _case() -> FuzzCase:
    schema = SchemaSpec(
        tables=(TableSpec(name="t0", columns=(ColSpec(name="c0", kind="integer"),)),)
    )
    return FuzzCase(
        schema_=schema,
        rows={"t0": [{"id": 1, "c0": None}]},
        selects=(SelectSpec(table="t0", alias="a0", columns=("a0.c0",)),),
    )


def test_entry_roundtrips_through_yaml(tmp_path: Path) -> None:
    finding = Finding(kind="tlp", summary="boom", sqls=("SELECT 1;",), detail={"n": 1})
    entry = entry_for(_case(), [finding], seed=7)
    assert entry["violated"] == "tlp" and entry["fuzz_seed"] == 7 and entry["status"] == "open"
    assert entry["schema_sql"].startswith("CREATE TABLE t0")
    assert entry["seed_sql"] == "INSERT INTO t0 (id, c0) VALUES (1, NULL);\n"
    path = save(entry, tmp_path)
    assert path.name == f"{entry['id']}.yaml"
    assert load_all(tmp_path) == [yaml.safe_load(path.read_text(encoding="utf8"))]
    assert load_all(tmp_path / "missing") == []


def test_plan_findings_are_labelled() -> None:
    finding = Finding(kind="plan", summary="x", sqls=("SELECT 1;",))
    assert "default" in entry_for(_case(), [finding], 0)["plan"]


def test_every_saved_regression_is_complete() -> None:
    entries = load_all(DEFAULT_DIR)
    assert entries, "the regression corpus must not be empty"
    for e in entries:
        assert {"id", "violated", "schema_sql", "seed_sql", "queries", "status"} <= set(e)
        assert e["status"] == "fixed", f"{e['id']} is still open"
        assert e.get("fix"), f"{e['id']} has no description of its fix"
        assert e["queries"]
