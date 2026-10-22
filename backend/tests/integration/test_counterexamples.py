"""Minimal counterexamples against real PostgreSQL and MongoDB."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from schemashift.metrics.evaluate import _sample_options
from schemashift.models.evidence import Counterexample, CounterexampleGap
from schemashift.pipeline import compile_sql
from schemashift.verification import SandboxConfig, verify
from schemashift.verification.counterexample import counterexample_for, generate_counterexamples
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
NAMES = ["ecommerce", "blog", "university", "banking"]


@pytest.fixture
def config(pg_dsn: str, mongo_client: Any) -> SandboxConfig:
    return SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL)


def _compile(name: str) -> tuple[Any, Any]:
    folder = SAMPLES / name
    options = _sample_options(folder)
    result = compile_sql(
        (folder / "schema.sql").read_text("utf-8"),
        (folder / "queries.sql").read_text("utf-8"),
        "",
        options,
    )
    return result, options


@pytest.mark.parametrize("name", NAMES)
def test_every_broken_verdict_has_a_counterexample_or_a_documented_reason(
    name: str, config: SandboxConfig
) -> None:
    result, options = _compile(name)
    found, gaps = generate_counterexamples(result, options, config, seed=1)
    covered = {c.verdict_id for c in found} | {g.verdict_id for g in gaps}
    broken = [v.node_id for v in result.equivalence.final.verdicts if v.status == "BROKEN"]
    assert broken, f"{name} should have BROKEN verdicts to cover"
    assert [b for b in broken if b not in covered] == []
    assert all(len(g.reason) > 30 for g in gaps)  # a reason, not a placeholder
    for c in found:
        assert c.reproducible and c.minimal and c.tables_differing
        assert c.setup and all(s.startswith("INSERT INTO") for s in c.setup)
        assert c.steps[0].postgres and c.steps[0].mongo


def test_banking_cascade_counterexample_is_small_and_explains_both_sides(
    config: SandboxConfig,
) -> None:
    result, options = _compile("banking")
    verdict = next(
        v
        for v in result.equivalence.final.verdicts
        if v.node_id == "cascade_delete:transactions.account_id->accounts.id"
    )
    ce = counterexample_for(result, options, config, verdict, seed=1)
    assert isinstance(ce, Counterexample)
    assert ce.rows_in_setup <= 6  # accounts, transactions and the NOT NULL ancestors only
    step = ce.steps[0]
    assert step.sql and step.sql.startswith("DELETE FROM")
    assert "no cascade" in step.mongo
    assert "transactions" in ce.tables_differing
    assert "PostgreSQL 0 row(s) vs MongoDB 1" in ce.difference


def test_counterexamples_are_reproducible_for_a_seed(config: SandboxConfig) -> None:
    result, options = _compile("banking")
    verdict = next(
        v for v in result.equivalence.final.verdicts if v.node_id.startswith("set_null:")
    )
    a = counterexample_for(result, options, config, verdict, seed=5)
    b = counterexample_for(result, options, config, verdict, seed=5)
    assert isinstance(a, Counterexample) and a == b


def test_verify_reports_counterexamples_and_gaps(config: SandboxConfig) -> None:
    result, options = _compile("banking")
    report = verify(result, options, config, seed=1)
    assert report.counterexamples and report.counterexample_gaps
    off = verify(result, options, config, seed=1, counterexamples=False)
    assert off.counterexamples == [] and off.counterexample_gaps == []


def test_a_verdict_without_a_generator_returns_its_gap(config: SandboxConfig) -> None:
    result, options = _compile("banking")
    verdict = next(v for v in result.equivalence.final.verdicts if v.rule_id == "EQ-AUTOINC")
    out = counterexample_for(result, options, config, verdict, seed=1)
    assert isinstance(out, CounterexampleGap) and "ObjectId" in out.reason


def test_matching_queries_make_their_verdicts_tested(config: SandboxConfig) -> None:
    result, options = _compile("banking")
    report = verify(result, options, config, seed=1, counterexamples=False)
    assert report.evidence_upgrades
    verdicts = {v.node_id: v for v in result.equivalence.final.verdicts}
    for up in report.evidence_upgrades:
        assert verdicts[up.verdict_id].evidence == "UNVERIFIED" and up.level == "TESTED"
        assert "same result on PostgreSQL and MongoDB" in up.basis
