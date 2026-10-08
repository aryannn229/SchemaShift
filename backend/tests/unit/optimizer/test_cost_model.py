import pytest

from schemashift.optimizer import RelationshipFeatures, load_weights, score_relationship
from schemashift.optimizer.cost_model import hard_constraint, projected_parent_bytes, soft_factors
from schemashift.optimizer.weights import Weights
from schemashift.optimizer.weights import load_weights as _load

W = load_weights()


def feats(**kw) -> RelationshipFeatures:  # type: ignore[no-untyped-def]
    base = dict(
        relationship_id="fk:c.p_id->p.id",
        parent="p",
        child="c",
        cardinality="1:N",
        child_independent_access=0.0,
        read_together_ratio=0.0,
        child_write_frequency="low",
        estimated_child_count_per_parent=10,
        estimated_child_doc_size_bytes=100,
        parent_doc_size_bytes=100,
        child_has_other_parents=False,
        guarantees_needing_embedding=0,
    )
    base.update(kw)
    return RelationshipFeatures(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- weights
def test_default_weights_match_the_spec() -> None:
    assert W.threshold == 0.5
    assert W.soft.read_together == 0.30 and W.soft.shape == 0.20 and W.soft.guarantees == 0.20
    assert W.soft.independent_access == -0.20 and W.soft.write_frequency == -0.10
    assert W.hard.max_parent_doc_bytes == 4 * 1024 * 1024 and W.hard.safety_factor == 2
    assert W.hard.max_children_per_parent == 1000 and W.hard.small_children == 50
    assert W.shape.one_to_one == 1.0 and W.shape.one_to_many_small == 0.6
    assert W.defaults.child_independent_access == 0.3


def test_score_bounds_come_from_weights() -> None:
    assert W.raw_max == pytest.approx(0.7) and W.raw_min == pytest.approx(-0.3)


def test_weights_file_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    custom = tmp_path / "w.yaml"
    custom.write_text("threshold: 0.8\nsoft:\n  read_together: 0.5\n", encoding="utf-8")
    loaded = _load(custom)
    assert loaded.threshold == 0.8 and loaded.soft.read_together == 0.5
    assert loaded.soft.shape == 0.20  # unspecified values keep defaults


def test_weights_yaml_is_valid() -> None:
    assert isinstance(load_weights(), Weights)


# --------------------------------------------------------------- hard constraints
def test_hard_self_reference() -> None:
    result = score_relationship(feats(cardinality="self", read_together_ratio=1.0), W)
    assert result.decision == "REFERENCE" and result.hard_constraint == "self_reference"


def test_hard_unbounded_growth() -> None:
    f = feats(estimated_child_count_per_parent=1001, read_together_ratio=1.0)
    result = score_relationship(f, W)
    assert result.decision == "REFERENCE" and result.hard_constraint == "unbounded_growth"
    assert "1001" in result.reason


def test_unbounded_boundary_is_inclusive_of_1000() -> None:
    assert hard_constraint(feats(estimated_child_count_per_parent=1000), W) is None


def test_hard_document_size() -> None:
    f = feats(estimated_child_count_per_parent=900, estimated_child_doc_size_bytes=2500)
    assert projected_parent_bytes(f, W) == 100 + 900 * 2500 * 2
    result = score_relationship(f, W)
    assert result.hard_constraint == "document_size" and result.decision == "REFERENCE"


def test_document_size_just_under_the_ceiling_is_allowed() -> None:
    count = 100
    child = (W.hard.max_parent_doc_bytes - 100) // (count * 2)
    assert (
        hard_constraint(
            feats(estimated_child_count_per_parent=count, estimated_child_doc_size_bytes=child), W
        )
        is None
    )
    assert (
        hard_constraint(
            feats(estimated_child_count_per_parent=count, estimated_child_doc_size_bytes=child + 1),
            W,
        )
        is not None
    )


def test_hard_constraint_wins_over_a_high_score() -> None:
    f = feats(
        estimated_child_count_per_parent=5000,
        read_together_ratio=1.0,
        guarantees_needing_embedding=2,
    )
    result = score_relationship(f, W)
    assert result.score > 0.5 and result.decision == "REFERENCE"


def test_no_hard_constraint_for_normal_edges() -> None:
    assert hard_constraint(feats(), W) is None


# ------------------------------------------------------------------ score formula
def test_score_formula_exact() -> None:
    f = feats(
        read_together_ratio=0.8,
        child_independent_access=0.5,
        child_write_frequency="high",
        guarantees_needing_embedding=1,
        estimated_child_count_per_parent=10,
    )
    raw = 0.30 * 0.8 + 0.20 * 0.6 + 0.20 * (1 / 2) - 0.20 * 0.5 - 0.10 * 1.0
    result = score_relationship(f, W)
    assert result.raw == pytest.approx(raw, abs=1e-4)
    assert result.score == pytest.approx((raw + 0.3) / 1.0, abs=1e-4)


def test_shape_values() -> None:
    assert soft_factors(feats(cardinality="1:1"), W)["relationship_shape"] == pytest.approx(0.20)
    assert soft_factors(feats(estimated_child_count_per_parent=50), W)[
        "relationship_shape"
    ] == pytest.approx(0.12)
    assert soft_factors(feats(estimated_child_count_per_parent=51), W)["relationship_shape"] == 0


def test_guarantees_are_capped_at_two() -> None:
    two = soft_factors(feats(guarantees_needing_embedding=2), W)["guarantees_needing_embedding"]
    five = soft_factors(feats(guarantees_needing_embedding=5), W)["guarantees_needing_embedding"]
    assert two == five == pytest.approx(0.20)


@pytest.mark.parametrize(("freq", "penalty"), [("low", 0.0), ("med", 0.05), ("high", 0.10)])
def test_write_frequency_penalty(freq: str, penalty: float) -> None:
    got = soft_factors(feats(child_write_frequency=freq), W)["child_write_frequency"]
    assert got == pytest.approx(-penalty)


def test_threshold_boundary_embeds() -> None:
    # raw 0.2 -> normalized exactly 0.5
    f = feats(cardinality="1:1")  # shape 0.2, everything else 0
    result = score_relationship(f, W)
    assert result.score == pytest.approx(0.5) and result.decision == "EMBED"


def test_just_below_threshold_references() -> None:
    f = feats(cardinality="1:1", child_independent_access=0.05)
    assert score_relationship(f, W).decision == "REFERENCE"


def test_score_is_clamped() -> None:
    best = feats(cardinality="1:1", read_together_ratio=1.0, guarantees_needing_embedding=2)
    worst = feats(
        child_independent_access=1.0,
        child_write_frequency="high",
        estimated_child_count_per_parent=500,
    )
    # best: raw = 0.3 + 0.2 + 0.2 = 0.7 -> normalized exactly 1.0 (the maximum)
    assert score_relationship(best, W).score == pytest.approx(1.0)
    # worst: raw = -0.2 - 0.1 = -0.3 -> normalized exactly 0.0 (the minimum)
    assert score_relationship(worst, W).score == pytest.approx(0.0, abs=1e-9)
    for f in (best, worst):
        assert 0.0 <= score_relationship(f, W).score <= 1.0


def test_factors_sorted_by_strength_and_zero_factors_dropped() -> None:
    f = feats(read_together_ratio=1.0, child_independent_access=0.2)
    names = [x.name for x in score_relationship(f, W).factors]
    assert names[0] == "read_together_ratio"
    assert "guarantees_needing_embedding" not in names  # zero contribution is dropped


def test_custom_weights_change_the_decision() -> None:
    f = feats(read_together_ratio=1.0)
    default = score_relationship(f, W)
    strict = score_relationship(f, W.model_copy(update={"threshold": 0.99}))
    assert default.decision == "EMBED" and strict.decision == "REFERENCE"
