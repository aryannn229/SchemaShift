"""Characterization tests: exact scores, reasons and helper results of the optimizer.

They pin the numbers and wording the UI and the docs show, so a silent change (a flipped comparison, a
different rounding, a reworded reason) fails here. Found by mutation testing (see docs/METRICS.md).
"""

from __future__ import annotations

import pytest

from schemashift.models import Placement
from schemashift.optimizer import AccessHint
from schemashift.optimizer.cost_model import (
    hard_constraint,
    projected_parent_bytes,
    score_relationship,
    soft_factors,
)
from schemashift.optimizer.features import estimate_doc_bytes, extract_features, value_bytes
from schemashift.optimizer.weights import load_weights
from schemashift.optimizer.workload import seed_row_counts, write_target
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions, compile_sql

W = load_weights()
SQL = (
    "CREATE TABLE p (id INT PRIMARY KEY); "
    "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id) ON DELETE CASCADE); "
    "CREATE TABLE n (id INT PRIMARY KEY, p_id INT REFERENCES p(id)); "
    "CREATE TABLE e (id INT PRIMARY KEY, boss INT REFERENCES e(id));"
)
QUERIES = "SELECT * FROM c JOIN p ON c.p_id = p.id; SELECT id FROM c;"


def compiled(options: CompileOptions | None = None):  # type: ignore[no-untyped-def]
    return compile_sql(SQL, QUERIES, "", options or CompileOptions())


def feats():  # type: ignore[no-untyped-def]
    r = compiled()
    return extract_features(r.graph, r.ir, r.queries, (), {}, W)


# ----------------------------------------------------------------------------- features
def test_feature_values_and_their_sources() -> None:
    f = feats()["fk:c.p_id->p.id"]
    assert (f.cardinality, f.child_fk_nullable, f.child_has_other_parents) == ("1:N", False, False)
    assert (f.read_together_ratio, f.child_independent_access, f.child_write_frequency) == (
        0.5,
        0.5,
        "low",
    )
    assert (f.estimated_child_count_per_parent, f.estimated_child_doc_size_bytes) == (20, 48)
    assert f.parent_doc_size_bytes == 33 and f.guarantees_needing_embedding == 1
    assert f.sources == {
        "child_independent_access": "workload",
        "read_together_ratio": "workload",
        "child_write_frequency": "workload",
        "estimated_child_count_per_parent": "default",
    }


def test_defaults_when_no_workload_applies() -> None:
    f = feats()["fk:n.p_id->p.id"]  # no query touches n
    assert f.child_fk_nullable and f.guarantees_needing_embedding == 0
    assert (f.read_together_ratio, f.child_independent_access) == (0.5, 0.3)
    assert f.sources["child_independent_access"] == "default"
    assert f.sources["read_together_ratio"] == "default"
    assert feats()["fk:e.boss->e.id"].cardinality == "self"


def test_hints_beat_workload_and_defaults() -> None:
    r = compiled()
    hint = {
        "fk:c.p_id->p.id": AccessHint(
            child_independent_access=0.9,
            read_together_ratio=0.1,
            child_write_frequency="high",
            estimated_child_count_per_parent=7,
        )
    }
    f = extract_features(r.graph, r.ir, r.queries, (), hint, W)["fk:c.p_id->p.id"]
    assert (f.child_independent_access, f.read_together_ratio) == (0.9, 0.1)
    assert (f.child_write_frequency, f.estimated_child_count_per_parent) == ("high", 7)
    assert set(f.sources.values()) == {"hint"}


def test_seed_rows_drive_the_fan_out_estimate() -> None:
    r = compiled()
    seed = parse(
        "INSERT INTO p VALUES (1), (2);"
        "INSERT INTO c VALUES (1, 1), (2, 1), (3, 2), (4, 2), (5, 2), (6, 2);",
        "queries",
    ).queries
    f = extract_features(r.graph, r.ir, r.queries, seed, {}, W)["fk:c.p_id->p.id"]
    assert (
        f.estimated_child_count_per_parent == 3
        and f.sources["estimated_child_count_per_parent"] == "seed"
    )
    assert seed_row_counts(seed) == {"p": 2, "c": 6}
    assert seed_row_counts(parse("SELECT 1;", "queries").queries) == {}


@pytest.mark.parametrize(
    ("sql", "target"),
    [
        ("INSERT INTO c VALUES (1, 1);", "c"),
        ('UPDATE "Mixed" SET x = 1;', "Mixed"),
        ("DELETE FROM c WHERE id = 1;", "c"),
        ("SELECT 1 FROM c;", None),
    ],
)
def test_write_target(sql: str, target: str | None) -> None:
    (q,) = parse(sql, "queries").queries
    assert write_target(q) == target  # type: ignore[arg-type]


def test_write_frequency_buckets() -> None:
    def freq(n: int) -> str:
        r = compile_sql(SQL, "".join(f"INSERT INTO c VALUES ({i}, 1);" for i in range(n)) + QUERIES)
        return extract_features(r.graph, r.ir, r.queries, (), {}, W)[
            "fk:c.p_id->p.id"
        ].child_write_frequency

    assert [freq(0), freq(1), freq(2), freq(3)] == ["low", "med", "med", "high"]


def test_value_and_document_size_estimates() -> None:
    from schemashift.models.schema import NormalizedType

    assert value_bytes(NormalizedType(base="INTEGER")) == 4
    assert value_bytes(NormalizedType(base="BIGINT")) == 8
    assert value_bytes(NormalizedType(base="VARCHAR", length=30)) == 30
    assert value_bytes(NormalizedType(base="VARCHAR", length=500)) == 200  # capped
    assert value_bytes(NormalizedType(base="VARCHAR")) == 50
    assert value_bytes(NormalizedType(base="INTEGER", is_array=True)) == 20
    sizes = {t.name: estimate_doc_bytes(t) for t in compiled().schema_.tables.values()}
    assert sizes == {"p": 33, "c": 48, "n": 48, "e": 48}


# --------------------------------------------------------------------------- cost model
def test_scores_reasons_and_factors_of_each_relationship() -> None:
    fs = feats()
    c = score_relationship(fs["fk:c.p_id->p.id"], W)
    assert (c.decision, c.score, c.raw, c.hard_constraint) == ("EMBED", 0.57, 0.27, None)
    assert c.reason == "embed score 0.57 >= threshold 0.50"
    assert [(f.name, f.contribution) for f in c.factors] == [
        ("read_together_ratio", 0.15),
        ("relationship_shape", 0.12),
        ("child_independent_access", -0.1),
        ("guarantees_needing_embedding", 0.1),
    ]  # strongest first (by absolute size), ties by name; zero factors are left out
    n = score_relationship(fs["fk:n.p_id->p.id"], W)
    assert (n.decision, n.score, n.raw, n.hard_constraint) == (
        "REFERENCE",
        0.51,
        0.21,
        "optional_parent",
    )
    assert n.reason == (
        "hard constraint: the foreign key is nullable: a child row without a parent would have no "
        "document to live in and would be lost on embedding"
    )
    e = score_relationship(fs["fk:e.boss->e.id"], W)
    assert e.hard_constraint == "self_reference" and e.score == 0.39
    assert e.reason == (
        "hard constraint: self-referencing relationships form a hierarchy and are never embedded"
    )


def test_below_the_threshold_the_reason_says_so() -> None:
    f = feats()["fk:c.p_id->p.id"].model_copy(update={"read_together_ratio": 0.0})
    c = score_relationship(f, W)
    assert c.decision == "REFERENCE" and c.reason == f"embed score {c.score:.2f} < threshold 0.50"
    assert c.score < 0.5


def test_the_threshold_itself_embeds() -> None:
    f = feats()["fk:c.p_id->p.id"]
    base = score_relationship(f, W)
    at_threshold = W.model_copy(update={"threshold": base.score})
    assert score_relationship(f, at_threshold).decision == "EMBED"  # >= , not >
    above = W.model_copy(update={"threshold": base.score + 0.001})
    assert score_relationship(f, above).decision == "REFERENCE"


def test_the_normalised_score_is_clamped_to_the_unit_interval() -> None:
    f = feats()["fk:c.p_id->p.id"]
    high = f.model_copy(
        update={
            "read_together_ratio": 50.0,
            "child_independent_access": 0.0,
            "guarantees_needing_embedding": 2,
        }
    )
    low = f.model_copy(update={"read_together_ratio": -50.0})
    assert score_relationship(high, W).score == 1.0
    assert score_relationship(low, W).score == 0.0


def test_soft_factors_each_term() -> None:
    f = feats()["fk:c.p_id->p.id"]
    s = soft_factors(f, W)
    assert s == pytest.approx(
        {
            "read_together_ratio": 0.15,
            "relationship_shape": 0.12,
            "guarantees_needing_embedding": 0.1,
            "child_independent_access": -0.1,
            "child_write_frequency": 0.0,
        }
    )
    one_to_one = f.model_copy(update={"cardinality": "1:1"})
    assert soft_factors(one_to_one, W)["relationship_shape"] == pytest.approx(0.2)
    large = f.model_copy(update={"estimated_child_count_per_parent": 51})
    assert soft_factors(large, W)["relationship_shape"] == 0.0
    boundary = f.model_copy(update={"estimated_child_count_per_parent": 50})
    assert soft_factors(boundary, W)["relationship_shape"] == pytest.approx(0.12)
    high_write = f.model_copy(update={"child_write_frequency": "high"})
    assert soft_factors(high_write, W)["child_write_frequency"] == pytest.approx(-0.1)
    many = f.model_copy(update={"guarantees_needing_embedding": 5})
    assert soft_factors(many, W)["guarantees_needing_embedding"] == pytest.approx(
        0.2
    )  # capped at 2


def test_hard_constraints_and_their_wording() -> None:
    f = feats()["fk:c.p_id->p.id"]
    assert hard_constraint(f, W) is None
    unbounded = f.model_copy(update={"estimated_child_count_per_parent": 1001})
    assert hard_constraint(unbounded, W) == (
        "unbounded_growth",
        "about 1001 children per parent exceeds the 1000 limit (unbounded array growth)",
    )
    at_limit = f.model_copy(update={"estimated_child_count_per_parent": 1000})
    assert hard_constraint(at_limit, W) is None  # the limit itself is allowed
    big = f.model_copy(
        update={"estimated_child_count_per_parent": 1000, "estimated_child_doc_size_bytes": 3000}
    )
    name, why = hard_constraint(big, W) or ("", "")
    assert name == "document_size"
    assert why == "projected parent document of 5.7 MB exceeds the 4 MB ceiling"
    assert (
        projected_parent_bytes(f, W) == 33 + 20 * 48 * 2
    )  # parent + children x size x safety factor


# -------------------------------------------------------------------------------- planner
def plan(sql: str, overrides: dict[str, Placement] | None = None, queries: str = ""):  # type: ignore[no-untyped-def]
    return compile_sql(
        sql, queries, "", CompileOptions(relationship_overrides=overrides or {})
    ).plan


def test_plan_decisions_carry_score_factors_host_and_reason() -> None:
    p = compiled().plan
    c = p.decisions["fk:c.p_id->p.id"]
    assert (c.decision, c.score, c.host, c.overridden) == ("EMBED", 0.57, "p", False)
    assert c.reason == "embed score 0.57 >= threshold 0.50"
    assert [f.name for f in c.top_factors] == [
        "read_together_ratio",
        "relationship_shape",
        "child_independent_access",
        "guarantees_needing_embedding",
    ]
    n = p.decisions["fk:n.p_id->p.id"]
    assert n.decision == "REFERENCE" and n.host is None and "nullable" in n.reason


JUNCTION = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT NOT NULL REFERENCES a(id), b_id INT NOT NULL REFERENCES b(id), "
    "PRIMARY KEY (a_id, b_id));"
)
PAYLOAD = JUNCTION.replace("PRIMARY KEY (a_id, b_id)", "note TEXT, PRIMARY KEY (a_id, b_id)")


def test_junction_placements_and_reasons() -> None:
    key = "m2n:ab"
    plain = plan(JUNCTION).decisions[key]
    assert (plain.decision, plain.reason, plain.overridden) == (
        "REFERENCE",
        "junction kept as its own collection",
        False,
    )
    ref = plan(JUNCTION, {key: "REFERENCE"}).decisions[key]
    assert (ref.decision, ref.reason, ref.overridden) == (
        "REFERENCE",
        "user override: REFERENCE",
        True,
    )
    folded = plan(JUNCTION, {key: "REF_ARRAY"})
    d = folded.decisions[key]
    assert (d.decision, d.reason, d.overridden) == ("REF_ARRAY", "user override: REF_ARRAY", True)
    assert d.host == "a"  # the side with the lower fan-out (ties by relationship id)
    assert d.score == 0.0 or d.score > 0
    emb = plan(JUNCTION, {key: "EMBED"})
    assert emb.decisions[key].decision == "REFERENCE"
    assert f"override EMBED on {key} ignored: M:N is never fully embedded" in emb.warnings


def test_a_pure_junction_with_an_embedded_edge_folds_into_that_parent() -> None:
    p = plan(JUNCTION, {"fk:ab.a_id->a.id": "EMBED"})
    d = p.decisions["m2n:ab"]
    assert (d.decision, d.host) == ("REF_ARRAY", "a")
    assert d.reason == "pure junction folded into an array of references on a"
    assert (
        p.decisions["fk:ab.a_id->a.id"].reason
        == "junction ab is folded into an array of references"
    )
    payload = plan(PAYLOAD, {"fk:ab.a_id->a.id": "EMBED"}).decisions["m2n:ab"]
    assert (
        payload.decision == "REFERENCE" and payload.reason == "junction kept as its own collection"
    )


def test_override_wording() -> None:
    p = plan(SQL, {"fk:e.boss->e.id": "EMBED", "fk:c.p_id->p.id": "REFERENCE", "fk:nope": "EMBED"})
    assert (
        "override EMBED on fk:e.boss->e.id ignored: self-references cannot be embedded"
        in p.warnings
    )
    assert "override for unknown relationship 'fk:nope' ignored" in p.warnings
    assert p.decisions["fk:e.boss->e.id"].reason == "self-references cannot be embedded"
    ref = p.decisions["fk:c.p_id->p.id"]
    assert (ref.decision, ref.reason, ref.overridden) == (
        "REFERENCE",
        "user override: REFERENCE",
        True,
    )
    forced = plan(SQL, {"fk:c.p_id->p.id": "EMBED"}).decisions["fk:c.p_id->p.id"]
    assert (forced.decision, forced.reason, forced.overridden) == (
        "EMBED",
        "user override: EMBED",
        True,
    )


def test_nullable_override_wording() -> None:
    p = plan(SQL, {"fk:n.p_id->p.id": "EMBED"})
    assert p.decisions["fk:n.p_id->p.id"].reason == (
        "nullable foreign key: children without a parent cannot be embedded"
    )
    assert any(
        w.startswith("override EMBED on fk:n.p_id->p.id ignored: the foreign key is nullable")
        for w in p.warnings
    )


def test_conflict_resolution_wording() -> None:
    two_parents = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT PRIMARY KEY, a_id INT NOT NULL REFERENCES a(id) ON DELETE CASCADE, "
        "b_id INT NOT NULL REFERENCES b(id) ON DELETE CASCADE);"
    )
    p = plan(two_parents, {"fk:c.a_id->a.id": "EMBED", "fk:c.b_id->b.id": "EMBED"})
    assert (
        p.decisions["fk:c.a_id->a.id"].decision == "EMBED"
    )  # equal scores: the alphabetical winner
    loser = p.decisions["fk:c.b_id->b.id"]
    assert loser.reason == "c is embedded in a instead (at most one parent)"
    assert any(
        "fk:c.b_id->b.id was not applied: c is embedded in a instead" in w for w in p.warnings
    )

    cycle = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT NOT NULL UNIQUE);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT NOT NULL UNIQUE REFERENCES a(id) ON DELETE CASCADE);"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id) ON DELETE CASCADE;"
    )
    reasons = {d.reason for d in plan(cycle).decisions.values() if d.decision == "REFERENCE"}
    assert "embedding cycle: lowest-scoring edge of the cycle is referenced" in reasons

    chain = "CREATE TABLE t0 (id INT PRIMARY KEY);" + "".join(
        f"CREATE TABLE t{i} (id INT PRIMARY KEY REFERENCES t{i - 1}(id) ON DELETE CASCADE);"
        for i in range(1, 6)
    )
    deep = plan(chain)
    assert "nested embedding deeper than 3 levels" in {d.reason for d in deep.decisions.values()}

    cols = ", ".join(f"j{i} JSONB" for i in range(10))
    big_sql = (
        f"CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, "
        f"p_id INT NOT NULL REFERENCES p(id) ON DELETE CASCADE, {cols});"
    )
    big = compile_sql(
        big_sql,
        "",
        "",
        CompileOptions(
            access_hints={
                "fk:c.p_id->p.id": AccessHint(
                    estimated_child_count_per_parent=1000, read_together_ratio=1.0
                )
            },
            relationship_overrides={"fk:c.p_id->p.id": "EMBED"},
        ),
    ).plan
    assert (
        big.decisions["fk:c.p_id->p.id"].reason.startswith("hard constraint")
        or "exceeds 4 MB" in big.decisions["fk:c.p_id->p.id"].reason
    )
