import pytest

from schemashift.models import Placement
from schemashift.optimizer import AccessHint, OptimizerOptions
from schemashift.pipeline import CompileOptions, compile_sql

CASC = "ON DELETE CASCADE"


def plan(sql: str, queries: str = "", overrides=None, hints=None):  # type: ignore[no-untyped-def]
    options = CompileOptions(relationship_overrides=overrides or {}, access_hints=hints or {})
    return compile_sql(sql, queries, "", options).plan


def decisions(p) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {k: v.decision for k, v in p.decisions.items()}


# ----------------------------------------------------------------------- basics
def test_every_relationship_gets_a_decision_with_factors() -> None:
    p = plan(
        f"CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id) {CASC});"
    )
    d = p.decisions["fk:c.p_id->p.id"]
    assert d.decision == "EMBED" and d.host == "p" and d.score >= 0.5 and d.top_factors
    assert "threshold" in d.reason


def test_default_one_to_many_without_guarantees_is_referenced() -> None:
    p = plan(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id));"
    )
    assert p.decisions["fk:c.p_id->p.id"].decision == "REFERENCE"


def test_one_to_one_is_embedded_by_default() -> None:
    p = plan(
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (p_id INT PRIMARY KEY REFERENCES p(id));"
    )
    assert p.decisions["fk:c.p_id->p.id"].decision == "EMBED"


def test_planning_is_deterministic() -> None:
    sql = (
        f"CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        f"CREATE TABLE r (id INT PRIMARY KEY, a_id INT REFERENCES a(id) {CASC}, b_id INT REFERENCES b(id) {CASC});"
    )
    assert plan(sql).model_dump() == plan(sql).model_dump()


# ------------------------------------------------------------ hard constraints
def test_self_reference_never_embedded() -> None:
    p = plan(f"CREATE TABLE e (id INT PRIMARY KEY, m INT UNIQUE REFERENCES e(id) {CASC});")
    d = p.decisions["fk:e.m->e.id"]
    assert d.decision == "REFERENCE" and "self-referencing" in d.reason


def test_unbounded_growth_forces_reference() -> None:
    sql = f"CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id) {CASC});"
    hint = {"fk:c.p_id->p.id": AccessHint(estimated_child_count_per_parent=2000)}
    d = plan(sql, hints=hint).decisions["fk:c.p_id->p.id"]
    assert d.decision == "REFERENCE" and "unbounded" in d.reason


def test_size_ceiling_forces_reference() -> None:
    cols = ", ".join(f"j{i} JSONB" for i in range(10))
    sql = f"CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id) {CASC}, {cols});"
    hint = {
        "fk:c.p_id->p.id": AccessHint(
            estimated_child_count_per_parent=1000, read_together_ratio=1.0
        )
    }
    d = plan(sql, hints=hint).decisions["fk:c.p_id->p.id"]
    assert d.score >= 0.5  # would embed on score alone
    assert d.decision == "REFERENCE" and "ceiling" in d.reason


# ------------------------------------------------------------ multi-parent
MULTI = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE r (id INT PRIMARY KEY, a_id INT REFERENCES a(id) {a}, b_id INT REFERENCES b(id) {b});"
)


def test_child_embeds_in_at_most_one_parent_the_best_scoring() -> None:
    hints = {"fk:r.b_id->b.id": AccessHint(read_together_ratio=1.0)}
    p = plan(MULTI.format(a=CASC, b=CASC), hints=hints)
    assert decisions(p)["fk:r.b_id->b.id"] == "EMBED"
    assert decisions(p)["fk:r.a_id->a.id"] == "REFERENCE"
    assert p.decisions["fk:r.a_id->a.id"].score >= 0.5  # it was a candidate, then lost
    assert "instead" in p.decisions["fk:r.a_id->a.id"].reason


def test_multi_parent_tie_breaks_alphabetically() -> None:
    p = plan(MULTI.format(a=CASC, b=CASC))
    assert (
        decisions(p)["fk:r.a_id->a.id"] == "EMBED"
        and decisions(p)["fk:r.b_id->b.id"] == "REFERENCE"
    )


def test_multi_parent_forced_override_wins() -> None:
    p = plan(MULTI.format(a=CASC, b=CASC), overrides={"fk:r.b_id->b.id": "EMBED"})
    assert (
        decisions(p)["fk:r.b_id->b.id"] == "EMBED"
        and decisions(p)["fk:r.a_id->a.id"] == "REFERENCE"
    )


def test_two_forced_embeds_for_one_child_warn() -> None:
    p = plan(
        MULTI.format(a="", b=""), overrides={"fk:r.a_id->a.id": "EMBED", "fk:r.b_id->b.id": "EMBED"}
    )
    assert sum(d == "EMBED" for d in decisions(p).values()) == 1
    assert any("not applied" in w for w in p.warnings)


# ----------------------------------------------------------------- cycle breaking
def test_two_cycle_is_broken() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT UNIQUE);"
        f"CREATE TABLE b (id INT PRIMARY KEY, a_id INT UNIQUE REFERENCES a(id) {CASC});"
        f"ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id) {CASC};"
    )
    p = plan(sql)
    embedded = [k for k, d in decisions(p).items() if d == "EMBED"]
    assert len(embedded) == 1
    other = next(k for k in decisions(p) if k not in embedded)
    assert "cycle" in p.decisions[other].reason


def test_three_cycle_is_broken_at_lowest_score() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, c_id INT UNIQUE);"
        f"CREATE TABLE b (id INT PRIMARY KEY, a_id INT UNIQUE REFERENCES a(id) {CASC});"
        f"CREATE TABLE c (id INT PRIMARY KEY, b_id INT UNIQUE REFERENCES b(id) {CASC});"
        "ALTER TABLE a ADD FOREIGN KEY (c_id) REFERENCES c(id);"  # no cascade: lowest score
    )
    p = plan(sql)
    d = decisions(p)
    assert d["fk:a.c_id->c.id"] == "REFERENCE" and "cycle" in p.decisions["fk:a.c_id->c.id"].reason
    assert d["fk:b.a_id->a.id"] == "EMBED" and d["fk:c.b_id->b.id"] == "EMBED"


def test_self_loop_forced_embed_is_ignored_with_warning() -> None:
    p = plan(
        "CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));",
        overrides={"fk:e.m->e.id": "EMBED"},
    )
    assert decisions(p)["fk:e.m->e.id"] == "REFERENCE" and p.warnings


# ---------------------------------------------------------------- depth and size
def chain(n: int) -> str:
    sql = "CREATE TABLE t0 (id INT PRIMARY KEY);"
    for i in range(1, n + 1):
        sql += f"CREATE TABLE t{i} (id INT PRIMARY KEY REFERENCES t{i - 1}(id) {CASC});"
    return sql


def test_depth_three_is_allowed() -> None:
    d = decisions(plan(chain(3)))
    assert all(v == "EMBED" for v in d.values())


def test_depth_four_is_cut_at_the_deepest_edge() -> None:
    p = plan(chain(4))
    d = decisions(p)
    assert (
        d["fk:t4.id->t3.id"] == "REFERENCE"
        and "deeper than 3" in p.decisions["fk:t4.id->t3.id"].reason
    )
    assert [d[f"fk:t{i}.id->t{i - 1}.id"] for i in (1, 2, 3)] == ["EMBED"] * 3


def test_depth_five_cuts_repeatedly() -> None:
    d = decisions(plan(chain(5)))
    assert sum(v == "EMBED" for v in d.values()) <= 4
    assert d["fk:t5.id->t4.id"] == "REFERENCE"


def test_nested_size_limit_references_the_biggest_branch() -> None:
    cols = ", ".join(f"j{i} JSONB" for i in range(10))
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        f"CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id) {CASC});"
        f"CREATE TABLE g (id INT PRIMARY KEY, c_id INT REFERENCES c(id) {CASC}, {cols});"
    )
    hints = {
        "fk:c.p_id->p.id": AccessHint(estimated_child_count_per_parent=50),
        "fk:g.c_id->c.id": AccessHint(estimated_child_count_per_parent=50),
    }
    p = plan(sql, hints=hints)
    d = decisions(p)
    assert (
        d["fk:c.p_id->p.id"] == "REFERENCE" and "exceeds" in p.decisions["fk:c.p_id->p.id"].reason
    )
    assert d["fk:g.c_id->c.id"] == "EMBED"


# -------------------------------------------------------------------- junctions
JUNC = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT REFERENCES a(id) {c}, b_id INT REFERENCES b(id) {c} {extra}"
)


def pure_junction(c: str = CASC) -> str:
    return JUNC.format(c=c, extra="").replace("{c}", c) + ", PRIMARY KEY (a_id, b_id));"


def test_pure_junction_with_embed_candidate_folds_into_array_of_refs() -> None:
    p = plan(pure_junction())
    assert p.decisions["m2n:ab"].decision == "REF_ARRAY"
    assert p.decisions["m2n:ab"].host in ("a", "b")
    assert (
        decisions(p)["fk:ab.a_id->a.id"] == "REFERENCE"
        and decisions(p)["fk:ab.b_id->b.id"] == "REFERENCE"
    )
    assert (
        "folded" in p.decisions["fk:ab.a_id->a.id"].reason
        or "folded" in p.decisions["fk:ab.b_id->b.id"].reason
    )


def test_pure_junction_without_embed_candidate_stays_collection() -> None:
    p = plan(pure_junction(""))
    assert p.decisions["m2n:ab"].decision == "REFERENCE"


def test_junction_with_payload_embeds_through_the_fk_edge() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        f"CREATE TABLE ab (a_id INT REFERENCES a(id) {CASC}, b_id INT REFERENCES b(id), qty INT, PRIMARY KEY (a_id, b_id));"
    )
    p = plan(sql)
    assert (
        decisions(p)["fk:ab.a_id->a.id"] == "EMBED"
        and p.decisions["m2n:ab"].decision == "REFERENCE"
    )


def test_junction_override_ref_array() -> None:
    p = plan(pure_junction(""), overrides={"m2n:ab": "REF_ARRAY"})
    d = p.decisions["m2n:ab"]
    assert d.decision == "REF_ARRAY" and d.overridden and d.host in ("a", "b")


def test_junction_override_reference_beats_fold() -> None:
    p = plan(pure_junction(), overrides={"m2n:ab": "REFERENCE"})
    assert p.decisions["m2n:ab"].decision == "REFERENCE" and p.decisions["m2n:ab"].overridden


def test_junction_override_embed_is_rejected() -> None:
    p = plan(pure_junction(""), overrides={"m2n:ab": "EMBED"})
    assert p.decisions["m2n:ab"].decision == "REFERENCE"
    assert any("never fully embedded" in w for w in p.warnings)


def test_ref_array_host_is_the_lower_fanout_side() -> None:
    hints = {
        "fk:ab.a_id->a.id": AccessHint(estimated_child_count_per_parent=500),
        "fk:ab.b_id->b.id": AccessHint(estimated_child_count_per_parent=5),
    }
    p = plan(pure_junction(""), overrides={"m2n:ab": "REF_ARRAY"}, hints=hints)
    assert p.decisions["m2n:ab"].host == "b"


# --------------------------------------------------------------------- overrides
BASIC = (
    "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id)"
    + " {a});"
)


def test_override_embed_on_a_low_score_edge() -> None:
    p = plan(BASIC.format(a=""), overrides={"fk:c.p_id->p.id": "EMBED"})
    d = p.decisions["fk:c.p_id->p.id"]
    assert d.decision == "EMBED" and d.overridden and "override" in d.reason


def test_override_reference_on_a_high_score_edge() -> None:
    p = plan(BASIC.format(a=CASC), overrides={"fk:c.p_id->p.id": "REFERENCE"})
    d = p.decisions["fk:c.p_id->p.id"]
    assert d.decision == "REFERENCE" and d.overridden


def test_override_unknown_relationship_warns() -> None:
    p = plan(BASIC.format(a=""), overrides={"fk:nope.x->y.z": "EMBED"})
    assert any("unknown relationship" in w for w in p.warnings)


def test_override_embed_still_respects_cycle_breaking() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT UNIQUE);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT UNIQUE REFERENCES a(id));"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id);"
    )
    p = plan(sql, overrides={"fk:b.a_id->a.id": "EMBED", "fk:a.b_id->b.id": "EMBED"})
    assert sum(d == "EMBED" for d in decisions(p).values()) == 1
    assert p.warnings


def test_options_object_accepts_weights() -> None:
    from schemashift.optimizer import load_weights

    strict = load_weights().model_copy(update={"threshold": 0.99})
    r = compile_sql(BASIC.format(a=CASC))
    assert r.plan.decisions["fk:c.p_id->p.id"].decision == "EMBED"
    from schemashift.optimizer import plan_placement

    again = plan_placement(r.graph, r.ir, options=OptimizerOptions(weights=strict))
    assert again.decisions["fk:c.p_id->p.id"].decision == "REFERENCE"


def test_plan_feeds_equivalence_final_pass() -> None:
    r = compile_sql(BASIC.format(a=CASC))
    changed = {c.node_id for c in r.equivalence.changed_by_placement}
    assert "cascade_delete:c.p_id->p.id" in changed
    assert r.equivalence.final.verdict_for("cascade_delete:c.p_id->p.id").status == "SAFE"


@pytest.mark.parametrize("value", ["EMBED", "REFERENCE"])
def test_overrides_typed_as_placement(value: Placement) -> None:
    assert (
        CompileOptions(relationship_overrides={"fk:x": value}).relationship_overrides["fk:x"]
        == value
    )
