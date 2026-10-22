"""Characterization tests for the equivalence package: wording, type map, predicate analysis, context.

Found by mutation testing: these pin behaviour (a flipped condition, a reworded reason) that the branch tests
alone let through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot

from schemashift.equivalence import RuleContext, RuleOptions, check_both, rules_doc
from schemashift.equivalence import registry as reg
from schemashift.equivalence.evidence import EVIDENCE, _c, evidence_for
from schemashift.equivalence.predicates import analyze_predicate
from schemashift.equivalence.typemap import TYPE_MAP, type_info
from schemashift.ir.nodes import IRNode
from schemashift.models import PlacementPlan
from schemashift.models.schema import NormalizedType
from tests.unit.equivalence.helpers import compile_ir

DOC = Path(__file__).resolve().parents[4] / "docs" / "RULES.md"


# ---------------------------------------------------------------------------- RULES.md
def test_rules_doc_matches_the_committed_file_exactly() -> None:
    """Kills every text mutation in rules, outcomes, evidence and the type map at once."""
    assert rules_doc.render() == DOC.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_rules_doc_main_writes_and_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "RULES.md"
    monkeypatch.setattr(rules_doc, "DOC_PATH", target)
    assert rules_doc.main(["--check"]) == 1  # missing file counts as stale
    assert (
        "docs/RULES.md is stale; run: python -m schemashift.equivalence.rules_doc"
        in capsys.readouterr().err
    )
    assert rules_doc.main([]) == 0 and target.read_text(encoding="utf-8") == rules_doc.render()
    assert b"\r\n" not in target.read_bytes()  # always LF
    assert rules_doc.main(["--check"]) == 0
    target.write_text("old", encoding="utf-8")
    assert rules_doc.main(["--check"]) == 1


# -------------------------------------------------------------------------------- registry
def test_registering_a_second_rule_for_a_node_type_fails() -> None:
    spec = next(r for r in reg.all_rules() if r.rule_id == "EQ-NOT-NULL")
    with pytest.raises(ValueError, match="duplicate rule for mongodb/NotNullGuarantee"):
        reg.rule(node_type=spec.node_type, rule_id="EQ-X", title="t", outcomes=spec.outcomes)(
            spec.fn
        )


def test_duplicate_outcome_keys_are_rejected() -> None:
    class Probe(IRNode):
        pass

    o = reg.Outcome("A", "when", "SAFE", "reason")
    with pytest.raises(ValueError, match="EQ-DUP: duplicate outcome keys"):
        reg.rule(node_type=Probe, rule_id="EQ-DUP", title="t", outcomes=(o, o))(
            lambda *a: ("A", {})
        )
    assert ("mongodb", Probe) not in reg._REGISTRY


def test_rules_are_looked_up_by_id_and_outcome() -> None:
    spec = reg.get_rule_by_id("EQ-CASCADE-DEL")
    assert spec.node_type.__name__ == "CascadingDelete" and spec.target == "mongodb"
    assert spec.outcome("CHAIN").status == "BROKEN"
    with pytest.raises(KeyError, match="EQ-CASCADE-DEL: unknown outcome 'NOPE'"):
        spec.outcome("NOPE")
    with pytest.raises(KeyError):
        reg.get_rule_by_id("EQ-NOPE")
    assert [r.rule_id for r in reg.all_rules()] == sorted(r.rule_id for r in reg.all_rules())
    assert reg.all_rules("some-other-target") == []


def test_trace_records_each_check_and_returns_its_result() -> None:
    t = reg.Trace()
    assert t.check("a", True) is True and t.check("b", False, "why") is False
    assert [(c.condition, c.result, c.detail) for c in t.items] == [
        ("a", True, None),
        ("b", False, "why"),
    ]


def test_verdict_inputs_name_the_placement_and_the_options() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY); "
        "CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id));"
    )
    fk = "fk:c.p_id->p.id"
    graph, program = compile_ir(sql)
    node = next(n for n in program.nodes if getattr(n, "fk_ref", None) == fk)
    ctx = RuleContext(graph=graph)

    def pairs(rule_id: str, c: RuleContext) -> list[tuple[str, str]]:
        return [(i.name, i.value) for i in reg._inputs(rule_id, node, c)]

    assert pairs("EQ-X", ctx) == [
        ("IR node", node.id),
        (f"placement of {fk}", "REFERENCE (default)"),
    ]
    assert ("option transactions_available", "True") in pairs("EQ-ATOMIC", ctx)
    assert ("option preserve_integer_ids", "False") in pairs("EQ-AUTOINC", ctx)
    assert ("option uuid_as", "binary") in pairs("EQ-TYPE", ctx)
    assert not any(n.startswith("option") for n, _ in pairs("EQ-X", ctx))
    # an explicit plan entry names its optimizer score
    from schemashift.models import PlacementDecision

    scored = PlacementPlan(
        decisions={fk: PlacementDecision(relationship_id=fk, decision="EMBED", score=0.314)}
    )
    assert pairs("EQ-X", RuleContext(graph=graph, plan=scored))[1] == (
        f"placement of {fk}",
        "EMBED (optimizer score 0.31)",
    )
    # a node without a foreign key has no placement input
    entity = next(n for n in program.nodes if not hasattr(n, "fk_ref"))
    assert [i.name for i in reg._inputs("EQ-X", entity, ctx)] == ["IR node"]


# ----------------------------------------------------------------------------- evidence
def test_evidence_helper_builds_structured_conditions() -> None:
    c = _c("kind", "text", max_items=5, flag=True)
    assert (c.kind, c.text, c.parameters) == ("kind", "text", {"max_items": 5, "flag": True})
    assert evidence_for("EQ-CASCADE-DEL", "REFERENCED").level == "PROVEN"
    assert evidence_for("nope", "nope").level == "UNVERIFIED"
    assert len(EVIDENCE) == sum(len(r.outcomes) for r in reg.all_rules())


# ------------------------------------------------------------------------------- typemap
EXPECTED_BSON = {
    "SMALLINT": "int", "INTEGER": "int", "BIGINT": "long", "DECIMAL": "decimal", "FLOAT": "double",
    "DOUBLE": "double", "BOOLEAN": "bool", "TEXT": "string", "VARCHAR": "string", "CHAR": "string",
    "UUID": "binData", "DATE": "date", "TIMESTAMP": "date", "TIMESTAMPTZ": "date", "TIME": "string",
    "INTERVAL": "string", "JSON": "object", "JSONB": "object", "BYTEA": "binData", "ENUM": "string",
}  # fmt: skip
LOSSY = {"CHAR", "DATE", "TIMESTAMPTZ", "TIME", "INTERVAL"}


@pytest.mark.parametrize("base", sorted(EXPECTED_BSON))
def test_type_info_for_every_base_type(base: str) -> None:
    info = type_info(NormalizedType(base=base))  # type: ignore[arg-type]
    assert info.bson == EXPECTED_BSON[base]
    assert info.lossy is (base in LOSSY)
    assert info.note == TYPE_MAP[base].note  # type: ignore[index]
    assert (info.fix != "") is (base in LOSSY)


def test_type_info_decimal_scale_arrays_and_exactness() -> None:
    assert type_info(NormalizedType(base="DECIMAL", precision=10, scale=0)).lossy is False
    assert type_info(NormalizedType(base="DECIMAL")).lossy is False  # no scale given
    d = type_info(NormalizedType(base="DECIMAL", precision=10, scale=2))
    assert d.lossy and d.note == "Decimal128 does not enforce scale 2 (NUMERIC(10,2))"
    assert d.fix == "round and validate the scale in the application layer before writing"
    arr = type_info(NormalizedType(base="INTEGER", is_array=True))
    assert arr.bson == "array<int>" and not arr.lossy
    assert type_info(NormalizedType(base="DATE", is_array=True)).bson == "array<date>"


# --------------------------------------------------------------------------- predicates
def analysis(sql: str):  # type: ignore[no-untyped-def]
    return analyze_predicate(sqlglot.parse_one(sql, dialect="postgres"))


@pytest.mark.parametrize(
    ("sql", "translatable", "needs_expr", "columns", "fragments"),
    [
        ("a > 1", True, False, ("a",), ()),
        ("a > 1 AND b < 2 OR NOT c = 3", True, False, ("a", "b", "c"), ()),
        ("(a >= 0)", True, False, ("a",), ()),
        ("a < b", True, True, ("a", "b"), ()),
        ("a + 1 > 5", True, True, ("a",), ()),
        ("a / 2 = 1", True, True, ("a",), ()),
        ("-a < 0", True, False, ("a",), ()),
        ("LENGTH(a) > 3", True, False, ("a",), ()),
        ("a IN (1, 2, 3)", True, False, ("a",), ()),
        ("a BETWEEN 1 AND 5", True, False, ("a",), ()),
        ("a BETWEEN b AND 5", True, True, ("a", "b"), ()),
        ("a LIKE 'x%'", True, False, ("a",), ()),
        ("a IS NULL", True, False, ("a",), ()),
        ("a IS NOT NULL", True, False, ("a",), ()),
        ("a IN (SELECT x FROM t)", False, False, ("a", "x"), ("a IN (SELECT x FROM t)",)),
        ("LOWER(a) = 'x'", False, False, ("a",), ("LOWER(a)",)),
        ("a LIKE b", False, False, ("a", "b"), ("a LIKE b",)),
        ("UPPER(a) LIKE 'X'", False, False, ("a",), ("UPPER(a) LIKE 'X'",)),
        ("a > 1 AND LOWER(b) = 'x' AND LOWER(b) = 'y'", False, False, ("a", "b"), ("LOWER(b)",)),
        ("now() > a", False, False, ("a",), ("CURRENT_TIMESTAMP",)),
        ("a IN (1, LOWER(b))", False, False, ("a", "b"), ("LOWER(b)",)),
        ("a BETWEEN 1 AND LOWER(b)", False, True, ("a", "b"), ("LOWER(b)",)),
    ],
)
def test_predicate_analysis(
    sql: str,
    translatable: bool,
    needs_expr: bool,
    columns: tuple[str, ...],
    fragments: tuple[str, ...],
) -> None:
    got = analysis(sql)
    assert got.translatable is translatable
    assert got.needs_expr is needs_expr
    assert got.columns == columns
    assert got.fragments == fragments


# ----------------------------------------------------------------------------- context
JUNCTION = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT NOT NULL REFERENCES a(id), b_id INT NOT NULL REFERENCES b(id), "
    "PRIMARY KEY (a_id, b_id));"
)


def test_context_follows_folded_junctions_and_embedding_chains() -> None:
    graph, _ = compile_ir(JUNCTION)
    folded = PlacementPlan.of({"m2n:ab": "REF_ARRAY"})
    host = {"m2n:ab": None}
    assert (
        RuleContext(graph=graph, plan=PlacementPlan.of({"m2n:ab": "REFERENCE"})).folded_host("ab")
        is None
    )
    assert RuleContext(graph=graph, plan=folded).folded_host("ab") is None  # no host recorded
    from schemashift.models import PlacementDecision

    with_host = PlacementPlan(
        decisions={
            "m2n:ab": PlacementDecision(relationship_id="m2n:ab", decision="REF_ARRAY", host="a")
        }
    )
    ctx = RuleContext(graph=graph, plan=with_host)
    assert ctx.folded_host("ab") == "a" and ctx.folded_host("a") is None and host
    assert ctx.is_embedded("fk:ab.a_id->a.id") is True  # folded into its parent counts as embedded
    assert ctx.is_embedded("fk:ab.b_id->b.id") is False
    assert ctx.is_embedded("fk:missing") is False
    assert ctx.is_table_embedded("ab") and not ctx.is_table_embedded("b")
    assert ctx.root_collection("ab") == "a" and ctx.root_collection("b") == "b"
    assert ctx.junction_decision("ab") == "REF_ARRAY"


def test_cascade_hops_follow_only_cascading_edges() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id) ON DELETE CASCADE);"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id) ON DELETE CASCADE);"
        "CREATE TABLE d (id INT PRIMARY KEY, c_id INT REFERENCES c(id));"
    )
    graph, _ = compile_ir(sql)
    ctx = RuleContext(graph=graph)
    first = graph.relationships["fk:b.a_id->a.id"]
    assert [r.id for r in ctx.cascade_hops(first)] == [
        "fk:c.b_id->b.id"
    ]  # d's edge does not cascade
    assert ctx.cascade_hops(graph.relationships["fk:d.c_id->c.id"]) == []


def test_default_rule_options_and_report_helpers() -> None:
    o = RuleOptions()
    assert (
        o.target_mongo_version,
        o.transactions_available,
        o.preserve_integer_ids,
        o.uuid_as,
    ) == (
        "7.0",
        True,
        False,
        "binary",
    )
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id));"
    graph, program = compile_ir(sql)
    both = check_both(program, graph, PlacementPlan.of({"fk:c.p_id->p.id": "EMBED"}))
    assert both.initial.pass_name == "initial" and both.final.pass_name == "final"
    assert [(c.node_id, c.initial, c.final) for c in both.changed_by_placement] == [
        ("pk:c.id", "SAFE", "CHANGED"),  # an embedded child's key is no longer index-enforced
        ("fk:c.p_id->p.id", "CHANGED", "SAFE"),  # but it cannot be orphaned any more
    ]
    assert both.final.verdict_for("fk:c.p_id->p.id").status == "SAFE"
    with pytest.raises(KeyError):
        both.final.verdict_for("nope")
