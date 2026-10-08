"""The labeled corpus: integrity, and the Phase 4 acceptance numbers."""

import pytest

from schemashift.equivalence.registry import RuleSpec
from schemashift.metrics.evaluate import (
    EquivalenceMetrics,
    compile_case,
    evaluate_equivalence,
    format_report,
    load_corpus,
    resolve_labels,
)

CASES = load_corpus()


def test_corpus_has_at_least_25_schemas() -> None:
    assert len(CASES) >= 25


def test_corpus_covers_the_required_shapes() -> None:
    names = " ".join(c.name for c in CASES)
    for needle in (
        "all_types",
        "one_to_one",
        "junction",
        "self_reference",
        "multi_parent",
        "cascade",
        "set_null",
        "restrict",
        "update_cascade",
        "uniqueness",
        "unique_embedded",
        "check",
        "defaults",
        "enums",
        "transactions",
        "joins",
        "aggregates",
        "circular",
        "semantic_errors",
        "ecommerce",
        "university",
        "banking",
    ):
        assert needle in names, needle
    realistic = [
        c for c in CASES if c.name.split("_", 1)[1] in {"ecommerce", "university", "banking"}
    ]
    for case in realistic:
        assert case.schema_sql.count("CREATE TABLE") >= 8, case.name


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_labels_are_well_formed(case) -> None:  # type: ignore[no-untyped-def]
    compiled = compile_case(case)
    node_ids = [n.id for n in compiled.program.nodes]
    labels, problems = resolve_labels(case, node_ids)
    assert problems.unknown_ids == [], "labels refer to nodes that do not exist"
    assert problems.dead_patterns == [], "bulk patterns that match nothing"
    assert problems.unlabeled == [], "every non-mechanical guarantee node must be labeled"
    assert all(label.why for label in labels.values()), "every label needs a justification"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_expected_diagnostics(case) -> None:  # type: ignore[no-untyped-def]
    compiled = compile_case(case)
    assert compiled.diagnostic_codes == set(case.expected.get("diagnostics") or [])


@pytest.fixture(scope="module")
def metrics() -> EquivalenceMetrics:
    result, _ = evaluate_equivalence(CASES)
    return result


def test_detection_rate_at_least_95_percent(metrics: EquivalenceMetrics) -> None:
    assert metrics.detection_rate >= 0.95, metrics.mismatches


def test_false_positive_rate_at_most_5_percent(metrics: EquivalenceMetrics) -> None:
    assert metrics.false_positive_rate <= 0.05, metrics.mismatches


def test_no_unexplained_mismatches(metrics: EquivalenceMetrics) -> None:
    assert metrics.mismatches == []


def test_corpus_exercises_all_three_statuses(metrics: EquivalenceMetrics) -> None:
    for status in ("SAFE", "CHANGED", "BROKEN"):
        assert sum(metrics.confusion[status].values()) >= 20  # type: ignore[index]


def test_report_formatting(metrics: EquivalenceMetrics) -> None:
    text = format_report(metrics)
    assert "detection rate" in text and "confusion matrix" in text


def test_metric_is_sensitive_to_rule_regressions(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a rule is broken, the corpus metrics must notice (guards against a vacuous metric)."""
    import schemashift.equivalence.registry as registry

    spec = registry.get_rule_by_id("EQ-CASCADE-DEL")
    broken = RuleSpec(
        spec.rule_id,
        spec.node_type,
        spec.title,
        spec.target,
        spec.outcomes,
        lambda node, ctx, trace: ("EMBEDDED", {"chain": ""}),  # always claims SAFE
    )
    monkeypatch.setitem(registry._REGISTRY, (spec.target, spec.node_type), broken)
    degraded, _ = evaluate_equivalence(CASES)
    assert degraded.detection_rate < 0.95
    assert degraded.mismatches


def test_metric_flags_false_positives(monkeypatch: pytest.MonkeyPatch) -> None:
    import schemashift.equivalence.registry as registry

    spec = registry.get_rule_by_id("EQ-ENTITY-UNIQ")
    noisy = RuleSpec(
        spec.rule_id,
        spec.node_type,
        spec.title,
        spec.target,
        spec.outcomes,
        lambda node, ctx, trace: ("PK_EMBEDDED", {"cols": ""}),  # always warns
    )
    monkeypatch.setitem(registry._REGISTRY, (spec.target, spec.node_type), noisy)
    degraded, _ = evaluate_equivalence(CASES)
    assert degraded.false_positive_rate > 0.0
