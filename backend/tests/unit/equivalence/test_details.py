import pytest
import sqlglot

from schemashift.equivalence import RuleContext, RuleOptions, check_both, check_program
from schemashift.equivalence.predicates import analyze_predicate
from schemashift.equivalence.registry import Outcome, Trace, all_rules, get_rule, rule
from schemashift.equivalence.rules_doc import DOC_PATH, main, render
from schemashift.equivalence.typemap import TYPE_MAP, type_info
from schemashift.ir.nodes import EntityNode
from schemashift.models import NormalizedType, PlacementPlan, worst
from tests.unit.equivalence.helpers import compile_ir, run, verdict


def pred(sql: str):  # type: ignore[no-untyped-def]
    return analyze_predicate(sqlglot.parse_one(sql, dialect="postgres"))


# ------------------------------------------------------------------ predicates
@pytest.mark.parametrize(
    "sql",
    [
        "a > 0",
        "a >= 0 AND a <= 10",
        "a IN (1, 2, 3)",
        "a BETWEEN 1 AND 5",
        "LENGTH(a) > 2",
        "a > 0 OR b < 3",
        "NOT (a = 1)",
        "a IS NOT NULL",
        "a = TRUE",
        "a > -1",
        "(a > 0)",
    ],
)
def test_translatable_predicates(sql: str) -> None:
    assert pred(sql).translatable and not pred(sql).needs_expr


@pytest.mark.parametrize("sql", ["a <= b", "a * 2 > b", "a + 1 > 3", "a BETWEEN b AND 5"])
def test_expr_predicates(sql: str) -> None:
    result = pred(sql)
    assert result.translatable and result.needs_expr


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("UPPER(a) = 'X'", "UPPER(a)"),
        ("a LIKE b", "a LIKE b"),
        ("a ILIKE 'x%'", "ILIKE"),
        ("a IN (SELECT 1)", "a IN (SELECT 1)"),
        ("a > 0 AND POSITION('x' IN b) > 0", "POSITION"),
        ("EXTRACT(YEAR FROM d) > 2000", "EXTRACT"),
    ],
)
def test_untranslatable_predicates(sql: str, fragment: str) -> None:
    result = pred(sql)
    assert not result.translatable
    assert any(fragment in f for f in result.fragments)


def test_predicate_columns() -> None:
    assert pred("a > b AND c IS NULL").columns == ("a", "b", "c")


# ---------------------------------------------------------------------- types
@pytest.mark.parametrize(
    ("base", "lossy"),
    [
        ("SMALLINT", False),
        ("INTEGER", False),
        ("BIGINT", False),
        ("DECIMAL", False),
        ("FLOAT", False),
        ("DOUBLE", False),
        ("BOOLEAN", False),
        ("TEXT", False),
        ("VARCHAR", False),
        ("CHAR", True),
        ("UUID", False),
        ("DATE", True),
        ("TIMESTAMP", False),
        ("TIMESTAMPTZ", True),
        ("TIME", True),
        ("INTERVAL", True),
        ("JSON", False),
        ("JSONB", False),
        ("BYTEA", False),
        ("ENUM", False),
    ],
)
def test_type_map_lossiness(base: str, lossy: bool) -> None:
    assert type_info(NormalizedType(base=base)).lossy is lossy  # type: ignore[arg-type]


def test_type_map_covers_every_base() -> None:
    assert len(TYPE_MAP) == 20


def test_decimal_scale_is_lossy_only_when_positive() -> None:
    assert type_info(NormalizedType(base="DECIMAL", precision=10, scale=2)).lossy
    assert not type_info(NormalizedType(base="DECIMAL", precision=10, scale=0)).lossy
    assert not type_info(NormalizedType(base="DECIMAL")).lossy


def test_array_type_uses_element_mapping() -> None:
    info = type_info(NormalizedType(base="TIMESTAMPTZ", is_array=True))
    assert info.bson == "array<date>" and info.lossy


def test_type_verdict_mentions_fix() -> None:
    v = verdict("CREATE TABLE t (a TIMESTAMPTZ);", "type:t.a")
    assert v.status == "CHANGED" and v.mitigation and "offset" in v.mitigation


def test_numeric_scale_verdict() -> None:
    assert verdict("CREATE TABLE t (a NUMERIC(10,2));", "type:t.a").status == "CHANGED"
    assert verdict("CREATE TABLE t (a NUMERIC(10));", "type:t.a").status == "SAFE"


def test_char_and_date_and_time_are_changed() -> None:
    for sql_type in ("CHAR(3)", "DATE", "TIME", "INTERVAL"):
        assert verdict(f"CREATE TABLE t (a {sql_type});", "type:t.a").status == "CHANGED"


# ---------------------------------------------------------------- rule details
def test_untranslatable_check_shows_fragment() -> None:
    v = verdict("CREATE TABLE t (a TEXT, CONSTRAINT ck CHECK (upper(a) = 'X'));", "check:t.ck")
    assert "UPPER(a)" in v.reason


def test_check_on_embedded_child_targets_nested_path() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id), n INT CHECK (n > 0));"
    )
    v = verdict(sql, "check:c.n_0", {"fk:c.pid->p.id": "EMBED"})
    assert v.status == "SAFE" and "nested path" in v.reason


def test_cascade_chain_reason_lists_full_chain() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id) ON DELETE CASCADE);"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id) ON DELETE CASCADE);"
        "CREATE TABLE d (id INT PRIMARY KEY, c_id INT REFERENCES c(id) ON DELETE CASCADE);"
    )
    v = verdict(sql, "cascade_delete:b.a_id->a.id")
    assert v.outcome == "CHAIN" and "a -> b -> c (by reference) -> d (by reference)" in v.reason


def test_cascade_cycle_does_not_loop() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id) ON DELETE CASCADE);"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id) ON DELETE CASCADE;"
    )
    assert run(sql).overall == "BROKEN"


def test_self_reference_cascade_does_not_loop() -> None:
    sql = "CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id) ON DELETE CASCADE);"
    assert verdict(sql, "cascade_delete:e.m->e.id").status == "BROKEN"


@pytest.mark.parametrize(
    ("agg_sql", "changed"),
    [
        ("SELECT COUNT(*) FROM c", False),
        ("SELECT COUNT(code) FROM p", False),
        ("SELECT MIN(n), MAX(n) FROM c", False),
        ("SELECT pid, SUM(n) FROM c GROUP BY pid", False),
        ("SELECT SUM(n) FROM c", True),
        ("SELECT AVG(n) FROM c", True),
        ("SELECT AVG(f) FROM c", False),
        ("SELECT AVG(amt) FROM c", True),
        ("SELECT p.code, SUM(c.n) FROM p LEFT JOIN c ON c.pid = p.id GROUP BY p.code", True),
        ("SELECT p.code, SUM(p.id) FROM p LEFT JOIN c ON c.pid = p.id GROUP BY p.code", False),
        ("SELECT pid FROM c GROUP BY pid", False),
    ],
)
def test_aggregate_rules(agg_sql: str, changed: bool) -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY, code TEXT);"
        "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id), n INT, f DOUBLE PRECISION,"
        " amt NUMERIC(8,2));" + agg_sql + ";"
    )
    assert (verdict(sql, "aggregate:q1").status == "CHANGED") is changed


def test_junction_with_both_sides_embeddable_keeps_default_reference() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (a_id INT REFERENCES a(id), b_id INT REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
    )
    assert verdict(sql, "cross_unique:ab.a_id,b_id").status == "SAFE"


def test_atomicity_nested_embedding_counts_as_one_document() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id));"
        "BEGIN; DELETE FROM a WHERE id = 1; DELETE FROM b WHERE id = 1; DELETE FROM c WHERE id = 1; COMMIT;"
    )
    plan = {"fk:b.a_id->a.id": "EMBED", "fk:c.b_id->b.id": "EMBED"}
    assert verdict(sql, "txn:t1", plan).outcome == "SINGLE_DOCUMENT"  # type: ignore[arg-type]


# --------------------------------------------------------------------- context
def test_root_collection_follows_embed_chain() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "CREATE TABLE c (id INT PRIMARY KEY, b_id INT REFERENCES b(id));"
    )
    graph, _ = compile_ir(sql)
    ctx = RuleContext(
        graph, PlacementPlan.of({"fk:b.a_id->a.id": "EMBED", "fk:c.b_id->b.id": "EMBED"})
    )
    assert (ctx.root_collection("c"), ctx.root_collection("b"), ctx.root_collection("a")) == (
        "a",
        "a",
        "a",
    )
    assert RuleContext(graph).root_collection("c") == "c"


def test_root_collection_survives_embed_cycle() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT REFERENCES a(id));"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id);"
    )
    graph, _ = compile_ir(sql)
    ctx = RuleContext(
        graph, PlacementPlan.of({"fk:b.a_id->a.id": "EMBED", "fk:a.b_id->b.id": "EMBED"})
    )
    assert ctx.root_collection("a") in ("a", "b")


def test_self_reference_is_never_treated_as_embedded() -> None:
    graph, _ = compile_ir("CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));")
    ctx = RuleContext(graph, PlacementPlan.of({"fk:e.m->e.id": "EMBED"}))
    assert ctx.embedding_relationship("e") is None


# ------------------------------------------------------------- checker + reports
SQL = (
    "CREATE TABLE p (id INT PRIMARY KEY);"
    "CREATE TABLE c (id INT PRIMARY KEY, pid INT REFERENCES p(id) ON DELETE CASCADE);"
)


def test_report_counts_and_overall() -> None:
    graph, program = compile_ir(SQL)
    report = check_program(program, graph)
    assert sum(report.counts.values()) == len(report.verdicts)
    assert report.overall == "BROKEN" and report.counts["BROKEN"] == 1


def test_overall_precedence() -> None:
    assert worst([]) == "SAFE"
    assert worst(["SAFE", "CHANGED"]) == "CHANGED"
    assert worst(["CHANGED", "BROKEN", "SAFE"]) == "BROKEN"


def test_clean_schema_overall_safe_or_changed() -> None:
    report = run("CREATE TABLE t (a INT NOT NULL);")
    assert report.overall == "SAFE"


def test_two_pass_flags_placement_changes() -> None:
    graph, program = compile_ir(SQL)
    result = check_both(program, graph, PlacementPlan.of({"fk:c.pid->p.id": "EMBED"}))
    assert result.initial.pass_name == "initial" and result.final.pass_name == "final"
    changed = {c.node_id: (c.initial, c.final) for c in result.changed_by_placement}
    assert changed["cascade_delete:c.pid->p.id"] == ("BROKEN", "SAFE")
    assert changed["fk:c.pid->p.id"] == ("CHANGED", "SAFE")
    assert "pk:p.id" not in changed


def test_two_pass_without_embedding_changes_nothing() -> None:
    graph, program = compile_ir(SQL)
    assert check_both(program, graph, PlacementPlan()).changed_by_placement == ()


def test_options_flow_into_rules() -> None:
    graph, program = compile_ir("CREATE TABLE t (a SERIAL);")
    report = check_program(program, graph, options=RuleOptions(preserve_integer_ids=True))
    assert report.verdict_for("auto_increment:t.a").outcome == "COUNTER"


def test_entity_nodes_have_no_rule() -> None:
    assert get_rule(EntityNode(id="e", origin="x", table="t")) is None


def test_verdict_for_missing_node() -> None:
    with pytest.raises(KeyError):
        run("CREATE TABLE t (a INT);").verdict_for("nope")


# --------------------------------------------------------------------- registry
def test_duplicate_rule_registration_rejected() -> None:
    from schemashift.ir.nodes import EnumDomain

    with pytest.raises(ValueError, match="duplicate rule"):
        rule(
            node_type=EnumDomain, rule_id="X", title="x", outcomes=(Outcome("A", "w", "SAFE", "r"),)
        )(lambda n, c, t: ("A", {}))


def test_duplicate_outcome_keys_rejected() -> None:
    class Dummy(EntityNode):
        pass

    with pytest.raises(ValueError, match="duplicate outcome"):
        rule(
            node_type=Dummy,
            rule_id="X",
            title="x",
            outcomes=(Outcome("A", "w", "SAFE", "r"), Outcome("A", "w", "SAFE", "r")),
        )(lambda n, c, t: ("A", {}))


def test_unknown_outcome_key_raises() -> None:
    spec = all_rules()[0]
    with pytest.raises(KeyError):
        spec.outcome("NOPE")


def test_trace_records_conditions() -> None:
    t = Trace()
    assert t.check("x", True, "d") is True
    assert t.items[0].condition == "x" and t.items[0].detail == "d"


def test_targets_are_scoped() -> None:
    from schemashift.ir.nodes import EnumDomain

    node = EnumDomain(id="e", origin="x", table="t", column="c", enum_name="m", values=("a",))
    assert get_rule(node, "mongodb") is not None
    assert get_rule(node, "dynamodb") is None


# ------------------------------------------------------------------- RULES.md
def test_rules_md_is_up_to_date() -> None:
    assert DOC_PATH.read_text(encoding="utf-8") == render(), (
        "docs/RULES.md is stale: run `python -m schemashift.equivalence.rules_doc`"
    )


def test_rules_doc_check_flag(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import schemashift.equivalence.rules_doc as rd

    fake = tmp_path / "RULES.md"
    monkeypatch.setattr(rd, "DOC_PATH", fake)
    assert main(["--check"]) == 1  # missing -> stale
    assert main([]) == 0
    assert main(["--check"]) == 0
    fake.write_text("old", encoding="utf-8")
    assert main(["--check"]) == 1


def test_rules_doc_mentions_every_outcome() -> None:
    text = render()
    for spec in all_rules():
        for o in spec.outcomes:
            assert f"{spec.rule_id} / {o.key} -> {o.status}" in text
