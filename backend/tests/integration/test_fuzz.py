"""Differential + metamorphic fuzzing against real PostgreSQL and MongoDB.

* every saved regression keeps passing (they are the minimal reproducers of bugs found by the fuzzer);
* the known limitation keeps reproducing, so that fixing it is noticed;
* a small fixed-seed fuzz budget is green (the nightly workflow runs a larger one).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from schemashift.fuzz.driver import check_case, run_fuzz
from schemashift.fuzz.harness import FuzzSession, InvalidCase
from schemashift.fuzz.regression import DEFAULT_DIR, load_all, replay
from schemashift.fuzz.spec import ColSpec, FuzzCase, SchemaSpec, SelectSpec, TableSpec
from schemashift.verification.sandbox import SandboxConfig
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration


@pytest.fixture
def config(pg_dsn: str, mongo_client: Any) -> SandboxConfig:
    return SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL)


REGRESSIONS = load_all(DEFAULT_DIR)


@pytest.mark.parametrize("entry", REGRESSIONS, ids=[e["id"] for e in REGRESSIONS])
def test_saved_regressions_replay_clean(entry: dict[str, Any], config: SandboxConfig) -> None:
    assert replay(entry, config) == [], entry["summary"]


def test_known_limitation_still_reproduces(config: SandboxConfig) -> None:
    """PostgreSQL's libc collation ignores punctuation at the first level; ICU does not."""
    path = next((DEFAULT_DIR / "known_limitations").glob("fuzz_*.yaml"))
    entry = yaml.safe_load(Path(path).read_text(encoding="utf8"))
    problems = replay(entry, config)
    assert problems and "differential" in problems[0], (
        "the collation limitation no longer reproduces: update docs/CODEGEN.md and "
        "move the reproducer to tests/regressions"
    )


def test_fixed_seed_budget_is_green(config: SandboxConfig) -> None:
    result = run_fuzz(config, cases_n=25, seed=2026, metamorphic=True)
    assert result.failures == [], result.failures
    assert result.cases > 0 and result.stats.relation_checks > 0


def test_a_good_case_has_no_findings(config: SandboxConfig) -> None:
    schema = SchemaSpec(
        tables=(TableSpec(name="t0", columns=(ColSpec(name="c0", kind="integer"),)),)
    )
    case = FuzzCase(
        schema_=schema,
        rows={"t0": [{"id": 1, "c0": 5}, {"id": 2, "c0": None}]},
        selects=(
            SelectSpec(table="t0", alias="a0", columns=("a0.c0",), order_by=(("a0.id", "ASC"),)),
        ),
    )
    findings, stats = check_case(case, config, metamorphic=True)
    assert findings == [] and stats.compared == 1


def test_invalid_cases_are_reported_not_run(config: SandboxConfig) -> None:
    with pytest.raises(InvalidCase):  # semantic error: foreign key to a table that does not exist
        FuzzSession(
            "CREATE TABLE a (id SERIAL, f INTEGER REFERENCES nope(id), PRIMARY KEY (id));",
            "",
            config,
        ).__enter__()
    with pytest.raises(InvalidCase):  # seed that PostgreSQL rejects
        FuzzSession(
            "CREATE TABLE a (id SERIAL, c INTEGER NOT NULL, PRIMARY KEY (id));",
            "INSERT INTO a (id, c) VALUES (1, NULL);",
            config,
        ).__enter__()


def test_select_outcome_statuses(config: SandboxConfig) -> None:
    schema = "CREATE TABLE a (id SERIAL, c INTEGER, PRIMARY KEY (id));"
    with FuzzSession(schema, "INSERT INTO a (id, c) VALUES (1, 1);", config) as s:
        assert s.select("SELECT a.id AS c0 FROM a a;").status == "MATCH"
        assert s.select("SELECT a.id AS c0 FROM a a WHERE a.nope = 1;").status in (
            "NOT_TRANSLATED",
            "PG_ERROR",
        )
        assert s.select("SELECT 1; SELECT 2;").status == "NOT_TRANSLATED"
        assert s.untranslated
