"""Evidence levels: the table is complete and consistent, and verdicts carry it."""

from __future__ import annotations

import pytest

from schemashift.equivalence import evidence as ev
from schemashift.equivalence.registry import all_rules
from schemashift.models.evidence import EVIDENCE_ORDER, EvidenceCondition
from schemashift.pipeline import CompileOptions, compile_sql

OUTCOMES = [(r.rule_id, o.key) for r in all_rules() for o in r.outcomes]


def test_every_outcome_has_an_evidence_entry_and_none_is_orphaned() -> None:
    assert set(OUTCOMES) == set(ev.EVIDENCE), (
        set(OUTCOMES) ^ set(ev.EVIDENCE)  # symmetric difference: missing or orphaned entries
    )


def test_table_shape_rules() -> None:
    assert ev.validate_table() == []
    levels = {s.level for s in ev.EVIDENCE.values()}
    assert levels <= set(EVIDENCE_ORDER)


def test_validate_table_detects_bad_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {
        ("X", "a"): ev.EvidenceSpec("PROVEN"),
        ("X", "b"): ev.EvidenceSpec("CONDITIONAL"),
        ("X", "c"): ev.EvidenceSpec("UNVERIFIED", argument="why"),
        ("X", "d"): ev.EvidenceSpec("TESTED", conditions=(EvidenceCondition(kind="k", text="t"),)),
    }
    monkeypatch.setattr(ev, "EVIDENCE", bad)
    problems = ev.validate_table()
    assert len(problems) == 4 and "PROVEN needs an argument" in problems[0]


def test_unknown_outcome_defaults_to_unverified() -> None:
    assert ev.evidence_for("EQ-NOPE", "X").level == "UNVERIFIED"


def test_conditions_are_structured_data() -> None:
    spec = ev.evidence_for("EQ-REF-INTEGRITY", "REFERENCED")
    assert spec.level == "CONDITIONAL"
    (cond,) = spec.conditions
    assert cond.kind == "app_enforcement" and "helpers" in cond.text
    assert ev.evidence_for("EQ-ATOMIC", "TRANSACTION").conditions[0].kind == "replica_set"


SCHEMA = (
    "CREATE TABLE p (id INT PRIMARY KEY, email TEXT UNIQUE);"
    "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id) ON DELETE CASCADE);"
)


REF = {"fk:c.p_id->p.id": "REFERENCE"}


def verdicts(plan: dict[str, str] | None = None, **opts: object):  # type: ignore[no-untyped-def]
    result = compile_sql(SCHEMA, "", "", CompileOptions(relationship_overrides=plan or {}, **opts))  # type: ignore[arg-type]
    return {v.node_id: v for v in result.equivalence.final.verdicts}


def test_verdicts_carry_evidence_argument_conditions_and_inputs() -> None:
    v = verdicts(REF)["cascade_delete:c.p_id->p.id"]  # referenced child: no native cascade
    assert (v.status, v.evidence) == ("BROKEN", "PROVEN")
    assert v.argument and "cascad" in v.argument.lower()
    assert v.decided_by.startswith("EQ-CASCADE-DEL/REFERENCED: ")
    assert any(i.name == "IR node" for i in v.inputs)
    placement = next(i for i in v.inputs if i.name.startswith("placement of "))
    assert "REFERENCE" in placement.value

    ri = verdicts(REF)["fk:c.p_id->p.id"]
    assert ri.evidence == "CONDITIONAL" and ri.argument is None
    assert [c.kind for c in ri.evidence_conditions] == ["app_enforcement"]


def test_the_plan_decision_is_named_in_the_inputs() -> None:
    v = verdicts({"fk:c.p_id->p.id": "EMBED"})["cascade_delete:c.p_id->p.id"]
    assert v.status == "SAFE" and v.evidence == "PROVEN"
    placement = next(i for i in v.inputs if i.name.startswith("placement of "))
    assert placement.value.startswith("EMBED")


def test_option_inputs_for_rules_that_consult_options() -> None:
    sql = "CREATE TABLE a (id SERIAL PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
    q = "BEGIN; INSERT INTO a (id) VALUES (1); INSERT INTO b (id, a_id) VALUES (1, 1); COMMIT;"
    r = compile_sql(sql, q, "", CompileOptions(transactions_available=False))
    atomic = next(v for v in r.equivalence.final.verdicts if v.rule_id == "EQ-ATOMIC")
    assert atomic.status == "BROKEN" and atomic.evidence == "PROVEN"
    assert any(
        i.name == "option transactions_available" and i.value == "False" for i in atomic.inputs
    )
    r2 = compile_sql(sql, q, "", CompileOptions(transactions_available=True))
    atomic2 = next(v for v in r2.equivalence.final.verdicts if v.rule_id == "EQ-ATOMIC")
    assert (
        atomic2.evidence == "CONDITIONAL" and atomic2.evidence_conditions[0].kind == "replica_set"
    )


def test_default_for_query_verdicts_is_unverified() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, n INT);"
    r = compile_sql(sql, "SELECT COUNT(*) AS c FROM t;")
    agg = next(v for v in r.equivalence.final.verdicts if v.rule_id == "EQ-AGG")
    assert agg.evidence == "UNVERIFIED" and agg.argument is None and agg.evidence_conditions == ()


def test_rules_doc_lists_evidence() -> None:
    from schemashift.equivalence.rules_doc import render

    doc = render()
    assert "CASCADE-DEL" in doc and "REFERENCED=BROKEN/PROVEN" in doc
    assert "- **Evidence:** CONDITIONAL" in doc and "Condition (app_enforcement)" in doc
