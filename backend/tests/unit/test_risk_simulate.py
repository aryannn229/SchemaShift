"""Phase 24: the risk score formula and the decision simulator."""

from __future__ import annotations

from pathlib import Path

import pytest

from schemashift.pipeline import compile_sql
from schemashift.risk import RISK_RULES, score_risk
from schemashift.risk.rules import CATEGORIES, FORMULA, level_of
from schemashift.simulate import simulate

SCHEMA = """
CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, age INT CHECK (age > 0));
CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, total NUMERIC(10,2));
CREATE TABLE notes (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id), body TEXT);
"""
QUERIES = (
    "SELECT u.email, o.total FROM users u JOIN orders o ON o.user_id = u.id WHERE o.total > 10;"
)
REL = "fk:orders.user_id->users.id"


def test_every_point_traces_to_a_finding_and_a_rule() -> None:
    rep = score_risk(compile_sql(SCHEMA, QUERIES))
    assert [c.id for c in rep.categories] == [c[0] for c in CATEGORIES]
    ids = {r.id for r in RISK_RULES}
    for cat in rep.categories:
        assert cat.points == sum(f.points for f in cat.findings)
        assert cat.score == min(100, round(100 * cat.points / cat.reference))
        for f in cat.findings:
            assert f.category == cat.id and f.detail and any(f.rule_id.startswith(i) for i in ids)
    assert rep.overall == round(sum(c.weight * c.score for c in rep.categories))
    assert rep.level == level_of(rep.overall) and rep.formula == FORMULA


def test_links_point_at_real_verdicts() -> None:
    result = compile_sql(SCHEMA, QUERIES)
    rep = score_risk(result)
    nodes = {v.node_id for v in result.equivalence.final.verdicts} | {
        v.node_id for v in result.concurrency.verdicts
    }
    loss = next(c for c in rep.categories if c.id == "constraint_loss")
    assert loss.findings and all(f.links[0] in nodes for f in loss.findings)


def test_scoring_is_deterministic_and_levels_have_the_documented_bounds() -> None:
    r = compile_sql(SCHEMA, QUERIES)
    assert score_risk(r).model_dump() == score_risk(r).model_dump()
    assert [level_of(x) for x in (0, 24, 25, 49, 50, 74, 75, 100)] == [
        "low",
        "low",
        "moderate",
        "moderate",
        "high",
        "high",
        "severe",
        "severe",
    ]


def test_untranslatable_queries_and_checks_add_points() -> None:
    r = compile_sql(SCHEMA, QUERIES)
    base = score_risk(r)
    worse = score_risk(r, untranslated_queries=["q1", "q2"], untranslated_checks=2)
    q = lambda rep, cid: next(c for c in rep.categories if c.id == cid).points  # noqa: E731
    assert q(worse, "query_incompatibility") == q(base, "query_incompatibility") + 16
    assert q(worse, "migration_complexity") == q(base, "migration_complexity") + 6
    assert worse.overall > base.overall


def test_a_table_without_a_primary_key_is_a_migration_finding() -> None:
    rep = score_risk(compile_sql("CREATE TABLE log (msg TEXT);"))
    cx = next(c for c in rep.categories if c.id == "migration_complexity")
    assert any(f.rule_id == "RISK-MIG-NO-KEY" and f.links == ("log",) for f in cx.findings)


# ----------------------------------------------------------------------------- simulator
def test_no_change_means_no_difference_and_the_optimizer_plan_is_untouched() -> None:
    sim = simulate(SCHEMA, QUERIES)
    assert sim.verdicts == () and sim.indexes.added == () == sim.indexes.removed
    assert sim.blocked == () and sim.risk.before == sim.risk.after


def test_flipping_a_relationship_changes_verdicts_indexes_pipelines_and_risk() -> None:
    base = simulate(SCHEMA, QUERIES)
    chosen = next(p for p in base.placements if p.relationship_id == REL).optimizer
    flipped = "REFERENCE" if chosen == "EMBED" else "EMBED"
    sim = simulate(SCHEMA, QUERIES, overrides={REL: flipped})  # type: ignore[dict-item]
    row = next(p for p in sim.placements if p.relationship_id == REL)
    assert (row.optimizer, row.simulated, row.overridden, row.blocked) == (
        chosen,
        flipped,
        True,
        False,
    )
    assert sim.label.startswith("simulated plan")
    assert sim.verdicts or sim.indexes.added or sim.indexes.removed or sim.validators_changed
    assert any(p.changed for p in sim.pipelines)
    assert sim.risk.before != sim.risk.after


def test_a_blocked_override_is_reported_with_its_reason() -> None:
    sim = simulate(
        "CREATE TABLE emp (id INT PRIMARY KEY, boss INT REFERENCES emp(id));",
        overrides={"fk:emp.boss->emp.id": "EMBED"},
    )
    (b,) = sim.blocked
    assert b.blocked and b.simulated != "EMBED" and b.blocked_reason


def test_constraint_toggle_through_alter_and_workload_frequency() -> None:
    sim = simulate(SCHEMA, QUERIES, alter_sql="ALTER TABLE users DROP CONSTRAINT users_email_key;")
    assert any(v.node_id == "unique:users.email" and v.after is None for v in sim.verdicts)
    assert sim.schema_changes and "UNIQUE" in sim.schema_changes[0]
    hot = simulate(SCHEMA, QUERIES, query_freq={"q1": 500})
    assert hot.verdicts == ()  # frequencies move scores, not guarantees
    assert all(not p.changed for p in hot.pipelines)  # same plan, same pipelines


@pytest.mark.parametrize("sample", ["banking", "blog"])
def test_samples_are_stable_under_a_noop_simulation(sample: str) -> None:
    d = Path(__file__).resolve().parents[3] / "samples" / sample
    sim = simulate((d / "schema.sql").read_text("utf-8"), (d / "queries.sql").read_text("utf-8"))
    assert sim.verdicts == () and sim.risk.before == sim.risk.after
