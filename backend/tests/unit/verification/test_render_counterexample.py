"""Counterexample building blocks that need no database."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest

from schemashift.equivalence.registry import all_rules
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification import counterexample as ce
from schemashift.verification.probes import PROBES
from schemashift.verification.render import literal, render_insert, render_sql

# ------------------------------------------------------------------------------ rendering


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (7, "7"),
        (Decimal("1.50"), "1.50"),
        (2.5, "2.5"),
        ("it's", "'it''s'"),
        (dt.date(2020, 1, 2), "'2020-01-02'"),
        (dt.datetime(2020, 1, 2, 3, 4, tzinfo=dt.UTC), "'2020-01-02T03:04:00+00:00'"),
        (dt.timedelta(seconds=90), "'90.0 seconds'"),
        (uuid.UUID(int=1), "'00000000-0000-0000-0000-000000000001'"),
        (b"\x01\xff", "'\\x01ff'"),
        ({"a": "it's"}, "'{\"a\": \"it''s\"}'"),
    ],
)
def test_literals(value: Any, text: str) -> None:
    assert literal(value) == text


def test_render_sql_inlines_parameters_in_order() -> None:
    sql = 'DELETE FROM "p" WHERE "a" = %s AND "b" = %s'
    assert render_sql(sql, (1, "x")) == 'DELETE FROM "p" WHERE "a" = 1 AND "b" = \'x\''
    assert render_sql("SELECT '100%%' WHERE a = %s", (5,)) == "SELECT '100%' WHERE a = 5"


def test_render_sql_does_not_guess_when_counts_differ() -> None:
    assert render_sql("SELECT %s, %s", (1,)) == "SELECT %s, %s"


def test_render_insert() -> None:
    assert (
        render_insert("t", {"id": 1, "n": None}) == 'INSERT INTO "t" ("id", "n") VALUES (1, NULL);'
    )


# --------------------------------------------------------------------------- minimal data
SCHEMA = """
CREATE TABLE root (id INT PRIMARY KEY);
CREATE TABLE mid (id INT PRIMARY KEY, root_id INT NOT NULL REFERENCES root(id) ON DELETE CASCADE,
                  other_id INT REFERENCES other(id));
CREATE TABLE leaf (id INT PRIMARY KEY, mid_id INT NOT NULL REFERENCES mid(id) ON DELETE CASCADE);
CREATE TABLE other (id INT PRIMARY KEY);
CREATE TABLE unrelated (id INT PRIMARY KEY, other_id INT REFERENCES other(id));
CREATE TABLE emp (id INT PRIMARY KEY, boss_id INT REFERENCES emp(id));
"""


def _result() -> Any:
    return compile_sql(SCHEMA, "", "", CompileOptions())


def _rel(result: Any, rid: str) -> Any:
    return result.graph.relationships[rid]


def test_closure_contains_only_what_the_example_needs() -> None:
    r = _result()
    rel = _rel(r, "fk:mid.root_id->root.id")
    assert ce._closure(r, rel, cascade_chain=False) == {"mid", "root"}
    # a cascading delete also needs the chain below the child
    assert ce._closure(r, rel, cascade_chain=True) == {"mid", "root", "leaf"}
    # NOT NULL ancestors are pulled in, nullable ones are not
    leaf_rel = _rel(r, "fk:leaf.mid_id->mid.id")
    assert ce._closure(r, leaf_rel, False) == {"leaf", "mid", "root"}


def test_minimal_seed_links_the_child_to_the_parent_and_nulls_other_keys() -> None:
    r = _result()
    rel = _rel(r, "fk:mid.root_id->root.id")
    seed = ce.minimal_seed(r, rel, seed=3)
    assert set(seed.rows) == {"root", "mid"}
    assert seed.rows["mid"][0]["root_id"] == seed.rows["root"][0]["id"]
    assert seed.rows["mid"][0]["other_id"] is None  # `other` is not in the closure
    assert [len(v) for v in seed.rows.values()] == [1, 1]
    # reproducible
    assert ce.minimal_seed(r, rel, seed=3).rows == seed.rows


def test_a_self_reference_needs_two_rows() -> None:
    r = _result()
    rel = _rel(r, "fk:emp.boss_id->emp.id")
    seed = ce.minimal_seed(r, rel, seed=1)
    assert len(seed.rows["emp"]) == 2
    assert seed.rows["emp"][1]["boss_id"] == seed.rows["emp"][0]["id"]


def test_link_is_a_noop_without_rows() -> None:
    ce._link({}, _rel(_result(), "fk:mid.root_id->root.id"))  # must not raise


# ------------------------------------------------------------------------------ gap table


def test_every_non_probeable_non_safe_outcome_has_a_documented_gap_reason() -> None:
    """No verdict may silently lack both a counterexample generator and a reason."""
    probeable = {t.__name__ for t in PROBES}
    missing: list[tuple[str, str]] = []
    for spec in all_rules():
        if spec.node_type.__name__ in probeable or spec.rule_id.startswith("EQ-CONC-"):
            continue  # concurrency scenarios are demonstrated by the concurrency probes instead
        for o in spec.outcomes:
            if o.status != "SAFE" and (spec.rule_id, o.key) not in ce.GAP_REASONS:
                missing.append((spec.rule_id, o.key))
    assert missing == []


def test_gap_reasons_refer_to_real_outcomes() -> None:
    real = {(s.rule_id, o.key) for s in all_rules() for o in s.outcomes}
    assert set(ce.GAP_REASONS) <= real
    assert all(len(reason) > 30 for reason in ce.GAP_REASONS.values())


# ------------------------------------------------------------------------- evidence upgrade
def test_matching_queries_upgrade_unverified_verdicts_to_tested() -> None:
    from schemashift.verification.runner import QueryOutcome, evidence_upgrades

    sql = "CREATE TABLE t (id INT PRIMARY KEY, n INT);"
    result = compile_sql(sql, "SELECT COUNT(*) AS c FROM t; SELECT n FROM t GROUP BY n;")
    agg = [v for v in result.equivalence.final.verdicts if v.evidence == "UNVERIFIED"]
    assert agg, "query-level verdicts start UNVERIFIED"

    def outcome(qid: str, status: str) -> Any:
        return QueryOutcome(query_id=qid, sql="", kind="SELECT", status=status)  # type: ignore[arg-type]

    ids = {getattr(result.ir.by_id(v.node_id), "query_id", None) for v in agg}
    done = [outcome(q, "MATCH") for q in ids if q]
    ups = evidence_upgrades(result, done, seed=7, rows={"t": 3})
    assert {u.verdict_id for u in ups} == {v.node_id for v in agg}
    assert all(u.level == "TESTED" and "seed 7" in u.basis and "3 rows" in u.basis for u in ups)
    # a mismatching or missing query upgrades nothing
    assert evidence_upgrades(result, [outcome(q, "MISMATCH") for q in ids if q], 7, {}) == []
    assert evidence_upgrades(result, [], 7, {}) == []
    # PROVEN / CONDITIONAL verdicts are never touched
    proven = [v for v in result.equivalence.final.verdicts if v.evidence != "UNVERIFIED"]
    assert not {u.verdict_id for u in ups} & {v.node_id for v in proven}
