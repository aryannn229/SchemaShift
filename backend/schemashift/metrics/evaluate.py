"""Evaluation over the labeled corpus (detection rate, false positives, severity accuracy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, cast

import yaml

from schemashift.equivalence import EquivalenceReport, RuleOptions, check_program
from schemashift.ir import IRProgram, build_ir
from schemashift.models import Placement, PlacementPlan, Status
from schemashift.parser import parse
from schemashift.semantic import AnalysisResult, analyze

STATUSES: tuple[Status, ...] = ("SAFE", "CHANGED", "BROKEN")
# Node types whose labels are optional in the corpus (they are mechanical and numerous).
OPTIONAL_LABEL_PREFIXES = ("type:", "not_null:")

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "tests" / "corpus"


@dataclass(frozen=True)
class Label:
    status: Status
    why: str


@dataclass
class CorpusCase:
    name: str
    schema_sql: str
    queries_sql: str
    seed_sql: str
    expected: dict[str, Any]

    @property
    def sql(self) -> str:
        return self.schema_sql + "\n" + self.queries_sql

    @property
    def placements(self) -> dict[str, Placement]:
        return cast(dict[str, Placement], self.expected.get("placements") or {})

    @property
    def options(self) -> RuleOptions:
        return RuleOptions(**(self.expected.get("options") or {}))


@dataclass
class CompiledCase:
    case: CorpusCase
    analysis: AnalysisResult
    program: IRProgram
    report: EquivalenceReport
    diagnostic_codes: set[str]


@dataclass
class Mismatch:
    case: str
    node_id: str
    label: Status
    predicted: Status
    why: str
    reason: str


@dataclass
class CaseProblems:
    unknown_ids: list[str] = field(default_factory=list)
    dead_patterns: list[str] = field(default_factory=list)
    unlabeled: list[str] = field(default_factory=list)


@dataclass
class EquivalenceMetrics:
    tp: int = 0
    fn: int = 0
    fp: int = 0
    tn: int = 0
    exact: int = 0
    total: int = 0
    confusion: dict[Status, dict[Status, int]] = field(
        default_factory=lambda: {a: {b: 0 for b in STATUSES} for a in STATUSES}
    )
    mismatches: list[Mismatch] = field(default_factory=list)
    diagnostic_mismatches: list[tuple[str, set[str], set[str]]] = field(default_factory=list)
    cases: int = 0

    @property
    def detection_rate(self) -> float:
        positives = self.tp + self.fn
        return self.tp / positives if positives else 1.0

    @property
    def false_positive_rate(self) -> float:
        negatives = self.fp + self.tn
        return self.fp / negatives if negatives else 0.0

    @property
    def severity_accuracy(self) -> float:
        return self.exact / self.total if self.total else 1.0


def load_corpus(root: Path = DEFAULT_CORPUS) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        schema = folder / "schema.sql"
        if not schema.exists():
            continue

        def read(name: str, folder: Path = folder) -> str:
            f = folder / name
            return f.read_text(encoding="utf-8") if f.exists() else ""

        expected = yaml.safe_load((folder / "expected.yaml").read_text(encoding="utf-8")) or {}
        cases.append(
            CorpusCase(
                name=folder.name,
                schema_sql=schema.read_text(encoding="utf-8"),
                queries_sql=read("queries.sql"),
                seed_sql=read("seed.sql"),
                expected=expected,
            )
        )
    return cases


def compile_case(case: CorpusCase) -> CompiledCase:
    parsed = parse(case.sql)
    analysis = analyze(parsed.schema_, parsed.queries)
    program = build_ir(analysis.graph, parsed.queries)
    plan = PlacementPlan.of(case.placements)
    report = check_program(program, analysis.graph, plan, case.options)
    codes = {d.code for d in parsed.diagnostics} | {d.code for d in analysis.diagnostics}
    return CompiledCase(case, analysis, program, report, codes)


def resolve_labels(case: CorpusCase, node_ids: list[str]) -> tuple[dict[str, Label], CaseProblems]:
    """Expand bulk patterns and explicit labels; report dangling ids and unlabeled nodes."""
    problems = CaseProblems()
    labels: dict[str, Label] = {}
    for entry in case.expected.get("bulk") or []:
        matched = [n for n in node_ids if fnmatchcase(n, entry["match"])]
        if not matched:
            problems.dead_patterns.append(entry["match"])
        for n in matched:
            labels[n] = Label(entry["status"], entry.get("why", ""))
    for node_id, entry in (case.expected.get("verdicts") or {}).items():
        if node_id not in node_ids:
            problems.unknown_ids.append(node_id)
            continue
        labels[node_id] = Label(entry["status"], entry.get("why", ""))
    if not case.expected.get("skip_coverage"):
        guarantee_ids = [
            n
            for n in node_ids
            if not n.startswith(("entity:", "attr:", "rel:"))
            and not n.startswith(OPTIONAL_LABEL_PREFIXES)
        ]
        problems.unlabeled = [n for n in guarantee_ids if n not in labels]
    return labels, problems


def evaluate_equivalence(
    cases: list[CorpusCase],
) -> tuple[EquivalenceMetrics, dict[str, CaseProblems]]:
    metrics = EquivalenceMetrics(cases=len(cases))
    problems: dict[str, CaseProblems] = {}
    for case in cases:
        compiled = compile_case(case)
        predicted = {v.node_id: v for v in compiled.report.verdicts}
        node_ids = [n.id for n in compiled.program.nodes]
        labels, case_problems = resolve_labels(case, node_ids)
        problems[case.name] = case_problems
        for node_id, label in labels.items():
            verdict = predicted.get(node_id)
            if verdict is None:
                case_problems.unknown_ids.append(f"{node_id} (no rule/verdict)")
                continue
            metrics.total += 1
            metrics.confusion[label.status][verdict.status] += 1
            if verdict.status == label.status:
                metrics.exact += 1
            else:
                metrics.mismatches.append(
                    Mismatch(
                        case.name, node_id, label.status, verdict.status, label.why, verdict.reason
                    )
                )
            if label.status == "SAFE":
                if verdict.status == "SAFE":
                    metrics.tn += 1
                else:
                    metrics.fp += 1
            elif verdict.status != "SAFE" and verdict.status == label.status:
                metrics.tp += 1
            else:
                metrics.fn += 1
        expected_codes = set(case.expected.get("diagnostics") or [])
        if compiled.diagnostic_codes != expected_codes:
            metrics.diagnostic_mismatches.append(
                (case.name, expected_codes, compiled.diagnostic_codes)
            )
    return metrics, problems


def format_report(metrics: EquivalenceMetrics) -> str:
    lines = [
        f"corpus schemas        : {metrics.cases}",
        f"labeled nodes         : {metrics.total}",
        f"detection rate        : {metrics.detection_rate:.1%}  (TP={metrics.tp} FN={metrics.fn})",
        f"false positive rate   : {metrics.false_positive_rate:.1%}"
        f"  (FP={metrics.fp} TN={metrics.tn})",
        f"severity accuracy     : {metrics.severity_accuracy:.1%}",
        "confusion matrix (rows = label, cols = predicted):",
        "            " + "".join(f"{s:>9}" for s in STATUSES),
    ]
    for label in STATUSES:
        lines.append(
            f"  {label:<9} " + "".join(f"{metrics.confusion[label][p]:>9}" for p in STATUSES)
        )
    return "\n".join(lines)


DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"


def _git_commit() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=DOCS_DIR,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _system_section() -> str:
    import json

    f = DOCS_DIR / "metrics.json"
    data: dict[str, Any] = json.loads(f.read_text("utf-8")) if f.exists() else {}
    rs = data.get("result_set") or {}
    ai = data.get("ai") or {}
    lines = []
    if rs.get("rate") is not None:
        lines.append(
            f"- **Result-set correctness:** {rs['rate']:.1%} ({rs['match']}/{rs['verified']} "
            "queries MATCH between PostgreSQL and MongoDB on the ecommerce, blog, university "
            "and banking samples)."
        )
    else:
        lines.append("- **Result-set correctness:** not measured (run `evaluate --verify`).")
    if ai.get("agreement") is not None:
        lines.append(
            f"- **AI agreement:** {ai['agreement']:.1%} over {ai['judged']} placements with a real model."
        )
    else:
        lines.append(
            "- **AI agreement:** not measured (needs `ANTHROPIC_API_KEY`; run `evaluate --ai`). "
            "The mock advisor never counts toward this metric."
        )
    return "\n".join(lines)


def write_metrics_doc(metrics: EquivalenceMetrics, path: Path | None = None) -> Path:
    """Write docs/METRICS.md with the latest numbers, date and commit."""
    from datetime import date

    target = path or DOCS_DIR / "METRICS.md"
    text = f"""# Metrics

Computed by `schemashift evaluate` over the labeled corpus in `backend/tests/corpus/`.

- **Date:** {date.today().isoformat()}
- **Commit:** {_git_commit()}

## Equivalence checker

```
{format_report(metrics)}
```

## System metrics (bundled samples)

{_system_section()}

## Definitions

| Metric | Definition |
|---|---|
| Detection rate | TP / (TP + FN). Positives are nodes labeled CHANGED or BROKEN; TP = predicted non-SAFE with the correct severity. |
| False positive rate | FP / (FP + TN). FP = node labeled SAFE but predicted CHANGED/BROKEN. |
| Severity accuracy | Fraction of labeled nodes whose predicted status equals the label. |
| Result-set correctness | MATCH queries / total verified queries (Phase 7). |
| AI agreement rate | Placements where AI == optimizer / placements with AI available (Phase 8). |

## How the labels were made, and their limits

Every corpus schema has an `expected.yaml` with a hand-written label and a one-line justification
per guarantee node (mechanical `type:*` / `not_null:*` nodes may be covered by explicit bulk
patterns). Labels were written from MongoDB semantics and the spec's rule table, **by the same
author as the rules**, so agreement is not independent validation: it shows the implementation is
internally consistent with the documented semantics. The corpus tests also check that the metric
is sensitive (deliberately broken rules make detection/FPR fail), that every non-mechanical node
is labeled, and that expected diagnostics match exactly.
"""
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


# ----------------------------------------------------------------- system metrics (Phases 7-8)
SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def _sample_options(folder: Path) -> Any:
    import json

    from schemashift.pipeline import CompileOptions

    f = folder / "options.json"
    raw = json.loads(f.read_text("utf-8")) if f.exists() else {}
    raw.pop("description", None)
    return CompileOptions.model_validate(raw)


def evaluate_system(
    samples: Path = SAMPLES_DIR,
    sandbox: Any = None,
    advisor: Any = None,
) -> dict[str, Any]:
    """Result-set correctness (needs ``sandbox``) and AI agreement (needs a real ``advisor``)."""
    from schemashift.ai import InMemoryCache, agreement_rate
    from schemashift.pipeline import compile_sql

    per_sample: dict[str, Any] = {}
    matched = total = 0
    judged: list[bool] = []
    for folder in sorted(p for p in samples.iterdir() if (p / "schema.sql").exists()):
        options = _sample_options(folder)
        queries = (
            (folder / "queries.sql").read_text("utf-8") if (folder / "queries.sql").exists() else ""
        )
        schema = (folder / "schema.sql").read_text("utf-8")
        result = compile_sql(
            schema, queries, "", options, advisor, InMemoryCache() if advisor else None
        )
        entry: dict[str, Any] = {}
        if advisor is not None:
            judged += [d.agree for d in result.plan.decisions.values() if d.agree is not None]
            rate = agreement_rate(result.plan)
            entry["ai_agreement"] = rate
        if sandbox is not None:
            from schemashift.verification import verify

            report = verify(result, options, sandbox, seed=1)
            done = report.verified
            ok = sum(o.status == "MATCH" for o in done)
            matched += ok
            total += len(done)
            entry["match"] = ok
            entry["verified"] = len(done)
            entry["unexplained"] = [o.query_id for o in report.unexplained]
        per_sample[folder.name] = entry
    return {
        "result_set": {
            "computed": sandbox is not None,
            "match": matched,
            "verified": total,
            "rate": matched / total if total else None,
        },
        "ai": {
            "computed": advisor is not None,
            "judged": len(judged),
            "agreement": sum(judged) / len(judged) if judged else None,
        },
        "samples": per_sample,
    }


def write_metrics_json(
    metrics: EquivalenceMetrics, system: dict[str, Any] | None, path: Path | None = None
) -> Path:
    """Write docs/metrics.json (served by GET /metrics). Sections not recomputed keep old values."""
    import json
    from datetime import date

    target = path or DOCS_DIR / "metrics.json"
    previous: dict[str, Any] = json.loads(target.read_text("utf-8")) if target.exists() else {}
    system = system or {}
    out: dict[str, Any] = {
        "date": date.today().isoformat(),
        "commit": _git_commit(),
        "equivalence": {
            "corpus_schemas": metrics.cases,
            "labeled_nodes": metrics.total,
            "detection_rate": metrics.detection_rate,
            "false_positive_rate": metrics.false_positive_rate,
            "severity_accuracy": metrics.severity_accuracy,
            "tp": metrics.tp,
            "fn": metrics.fn,
            "fp": metrics.fp,
            "tn": metrics.tn,
            "confusion": metrics.confusion,
        },
    }
    for key in ("result_set", "ai"):
        fresh = system.get(key)
        out[key] = fresh if fresh and fresh.get("computed") else previous.get(key, fresh)
    out["samples"] = system.get("samples") or previous.get("samples", {})
    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target
