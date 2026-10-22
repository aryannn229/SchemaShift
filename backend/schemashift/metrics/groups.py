"""Per-provenance metrics: precision / recall / F1, confusion matrix and the failure list.

The self-labeled and independently labeled corpora are scored separately and never merged
into one headline number (see ``labels.py`` for what counts as independent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from schemashift.metrics.evaluate import (
    DEFAULT_CORPUS,
    STATUSES,
    CorpusCase,
    EquivalenceMetrics,
    Mismatch,
    evaluate_equivalence,
    load_corpus,
)
from schemashift.metrics.labels import GROUPS, provenance_of

INDEPENDENT_CORPUS = DEFAULT_CORPUS.parent / "corpus_independent"


@dataclass(frozen=True)
class Prf:
    precision: float | None
    recall: float | None
    f1: float | None
    support: int


def _prf(tp: int, fp: int, fn: int) -> Prf:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None or recall is None else 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return Prf(precision, recall, f1, tp + fn)


def class_stats(m: EquivalenceMetrics) -> dict[str, Prf]:
    """One-vs-rest precision/recall/F1 for SAFE, CHANGED and BROKEN."""
    out: dict[str, Prf] = {}
    for s in STATUSES:
        tp = m.confusion[s][s]
        fp = sum(m.confusion[o][s] for o in STATUSES if o != s)
        fn = sum(m.confusion[s][p] for p in STATUSES if p != s)
        out[s] = _prf(tp, fp, fn)
    return out


def problem_stats(m: EquivalenceMetrics) -> Prf:
    """Problem detection: is a guarantee flagged (CHANGED or BROKEN) at all, severity aside."""
    tp = sum(m.confusion[a][p] for a in STATUSES if a != "SAFE" for p in STATUSES if p != "SAFE")
    fp = sum(m.confusion["SAFE"][p] for p in STATUSES if p != "SAFE")
    fn = sum(m.confusion[a]["SAFE"] for a in STATUSES if a != "SAFE")
    return _prf(tp, fp, fn)


@dataclass
class GroupResult:
    group: str
    schemas: list[str] = field(default_factory=list)
    metrics: EquivalenceMetrics = field(default_factory=EquivalenceMetrics)
    classes: dict[str, Prf] = field(default_factory=dict)
    problem: Prf = Prf(None, None, None, 0)
    labelers: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[Mismatch]:
        return self.metrics.mismatches


def load_all_cases(roots: list[Path] | None = None) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    seen: set[str] = set()
    for root in roots or [DEFAULT_CORPUS, INDEPENDENT_CORPUS]:
        if not root.exists():
            continue
        for case in load_corpus(root):
            if case.name in seen:
                raise ValueError(f"duplicate corpus case name {case.name!r}")
            seen.add(case.name)
            cases.append(case)
    return cases


def evaluate_groups(cases: list[CorpusCase] | None = None) -> dict[str, GroupResult]:
    """Evaluate every case, grouped by the provenance recorded in its label file."""
    cases = cases if cases is not None else load_all_cases()
    buckets: dict[str, list[CorpusCase]] = {g: [] for g in GROUPS}
    for case in cases:
        buckets[provenance_of(case.expected).group].append(case)
    results: dict[str, GroupResult] = {}
    for group, members in buckets.items():
        metrics, _ = evaluate_equivalence(members)
        results[group] = GroupResult(
            group=group,
            schemas=[c.name for c in members],
            metrics=metrics,
            classes=class_stats(metrics),
            problem=problem_stats(metrics),
            labelers=sorted({provenance_of(c.expected).labeled_by for c in members}),
        )
    return results


def fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def format_group(r: GroupResult) -> str:
    m = r.metrics
    if not r.schemas:
        return f"{r.group}: no labeled schemas yet"
    lines = [
        f"{r.group}: {len(r.schemas)} schemas, {m.total} labeled nodes "
        f"(labeled by: {', '.join(r.labelers)})",
        f"  problem detection   precision {fmt(r.problem.precision)}  "
        f"recall {fmt(r.problem.recall)}  F1 {fmt(r.problem.f1)}",
    ]
    for s in STATUSES:
        c = r.classes[s]
        lines.append(
            f"  {s:<8} precision {fmt(c.precision):>7}  recall {fmt(c.recall):>7}  "
            f"F1 {fmt(c.f1):>7}  (n={c.support})"
        )
    lines.append(f"  severity accuracy   {fmt(m.severity_accuracy)}")
    lines.append("  confusion (rows = label, cols = predicted):")
    lines.append("              " + "".join(f"{s:>9}" for s in STATUSES))
    for a in STATUSES:
        lines.append(f"    {a:<9} " + "".join(f"{m.confusion[a][p]:>9}" for p in STATUSES))
    return "\n".join(lines)


def to_json(r: GroupResult) -> dict[str, Any]:
    m = r.metrics
    return {
        "schemas": r.schemas,
        "labeled_by": r.labelers,
        "labeled_nodes": m.total,
        "problem_detection": r.problem.__dict__,
        "classes": {k: v.__dict__ for k, v in r.classes.items()},
        "severity_accuracy": m.severity_accuracy,
        "confusion": m.confusion,
        "failures": [
            {
                "case": f.case,
                "node": f.node_id,
                "label": f.label,
                "predicted": f.predicted,
                "label_why": f.why,
                "checker_reason": f.reason,
            }
            for f in r.failures
        ],
    }
