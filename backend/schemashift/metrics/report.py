"""Assemble every metric into one JSON document and render docs/METRICS.md from it.

Sections are independent: a run that cannot recompute one (no databases, no API key, no mutation
run) keeps the previous value of that section from docs/metrics.json instead of inventing one.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from schemashift.ai.mock_advisor import MockAdvisor
from schemashift.metrics import agreement as agr
from schemashift.metrics import groups as grp
from schemashift.metrics.evaluate import SAMPLES_DIR, _sample_options
from schemashift.metrics.external import evaluate_external
from schemashift.pipeline import CompileOptions

DOCS = Path(__file__).resolve().parents[3] / "docs"
JSON_PATH = DOCS / "metrics.json"
MD_PATH = DOCS / "METRICS.md"


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=DOCS,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_json(path: Path | None = None) -> dict[str, Any]:
    path = path or JSON_PATH
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def _compile_options(expected: dict[str, Any]) -> CompileOptions:
    """The label file's rule options, as pipeline options (unknown keys are ignored)."""
    keys = {"target_mongo_version", "transactions_available", "preserve_integer_ids", "uuid_as"}
    return CompileOptions(**{k: v for k, v in (expected.get("options") or {}).items() if k in keys})


def agreement_cases() -> list[tuple[str, str, str, Any]]:
    """Every corpus schema, bundled sample and external schema, as (name, schema, queries, options)."""
    cases: list[tuple[str, str, str, Any]] = []
    for c in grp.load_all_cases():
        cases.append(
            (f"corpus/{c.name}", c.schema_sql, c.queries_sql, _compile_options(c.expected))
        )
    roots = [SAMPLES_DIR, SAMPLES_DIR / "external"]
    for root in roots:
        if not root.exists():
            continue
        for folder in sorted(p for p in root.iterdir() if (p / "schema.sql").exists()):
            q = folder / "queries.sql"
            cases.append(
                (
                    f"{root.name}/{folder.name}"
                    if root != SAMPLES_DIR
                    else f"samples/{folder.name}",
                    (folder / "schema.sql").read_text("utf-8"),
                    q.read_text("utf-8") if q.exists() else "",
                    _sample_options(folder),
                )
            )
    return cases


def compute(
    *,
    sandbox: Any = None,
    real_advisor: Any = None,
    mutation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute everything that can be recomputed here; keep the rest from metrics.json."""
    previous = load_json()
    results = grp.evaluate_groups()
    cases = agreement_cases()
    mock = agr.summarize(agr.collect_pairs(cases, MockAdvisor()), "mock", real_model=False)
    agreement: dict[str, Any] = {"mock": agr.to_json(mock)}
    if real_advisor is not None:
        real = agr.summarize(agr.collect_pairs(cases, real_advisor), "anthropic", real_model=True)
        agreement["real"] = agr.to_json(real)
    else:
        agreement["real"] = (previous.get("agreement") or {}).get("real")
    external = evaluate_external(sandbox=sandbox)
    if sandbox is None:
        old = (previous.get("external") or {}).get("schemas", {})
        for name, entry in external["schemas"].items():
            if "result_set" in old.get(name, {}):
                entry["result_set"] = old[name]["result_set"]
        external["result_set"] = (previous.get("external") or {}).get(
            "result_set", external["result_set"]
        )
    data = dict(previous)
    legacy = results["self_labeled"].metrics
    data.update(
        {
            "date": date.today().isoformat(),
            "commit": git_commit(),
            "equivalence": {
                "corpus_schemas": legacy.cases,
                "labeled_nodes": legacy.total,
                "detection_rate": legacy.detection_rate,
                "false_positive_rate": legacy.false_positive_rate,
                "severity_accuracy": legacy.severity_accuracy,
                "tp": legacy.tp,
                "fn": legacy.fn,
                "fp": legacy.fp,
                "tn": legacy.tn,
                "confusion": legacy.confusion,
            },
            "self_labeled": grp.to_json(results["self_labeled"]),
            "independent": grp.to_json(results["independent"]),
            "agreement": agreement,
            "external": external,
        }
    )
    fresh = mutation if mutation is not None else load_mutation()
    if fresh is not None:
        data["mutation"] = fresh
    return data


def load_mutation(path: Path | None = None) -> dict[str, Any] | None:
    """The result of scripts/mutation.sh (docs/mutation.json), if a run has produced one."""
    path = path or DOCS / "mutation.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text("utf-8"))
    return {
        "score": raw["score"],
        "killed": raw["killed"],
        "total": raw["total"],
        "survived": raw["survived"] + raw.get("no_tests", 0),
        "timeout": raw.get("timeout", 0),
        "paths": raw["paths"],
        "command": raw["command"],
        "date": raw.get("date"),
    }


def write_json(data: dict[str, Any], path: Path | None = None) -> Path:
    path = path or JSON_PATH
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------------- markdown
def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _group_block(name: str, g: dict[str, Any]) -> str:
    if not g["schemas"]:
        return "No labeled schemas in this group yet.\n"
    lines = [
        f"{len(g['schemas'])} schemas, {g['labeled_nodes']} labeled guarantees; labeled by "
        f"{', '.join(g['labeled_by'])}.",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    pd = g["problem_detection"]
    lines.append(
        f"| problem detection (CHANGED or BROKEN) | {pct(pd['precision'])} | {pct(pd['recall'])} "
        f"| {pct(pd['f1'])} | {pd['support']} |"
    )
    for s in ("SAFE", "CHANGED", "BROKEN"):
        c = g["classes"][s]
        lines.append(
            f"| {s} | {pct(c['precision'])} | {pct(c['recall'])} | {pct(c['f1'])} | {c['support']} |"
        )
    lines += ["", f"Severity accuracy: {pct(g['severity_accuracy'])}.", "", "```"]
    lines.append("confusion (rows = label, cols = predicted)")
    lines.append(f"{'':<10}{'SAFE':>9}{'CHANGED':>9}{'BROKEN':>9}")
    for a in ("SAFE", "CHANGED", "BROKEN"):
        row = g["confusion"][a]
        lines.append(f"{a:<10}{row['SAFE']:>9}{row['CHANGED']:>9}{row['BROKEN']:>9}")
    lines += ["```", ""]
    return "\n".join(lines)


def _external_table(ext: dict[str, Any]) -> str:
    lines = [
        "| Schema | Tables | Guarantees (SAFE/CHANGED/BROKEN) | Parse/analysis diagnostics | Queries translated | Result sets MATCH |",
        "|---|---|---|---|---|---|",
    ]
    for name, e in ext["schemas"].items():
        v = e["verdicts"]
        diag = ", ".join(f"{k}×{n}" for k, n in list(e["diagnostics"].items())[:4]) or "none"
        rs = e.get("result_set")
        if rs is None:
            match = "not run"
        elif "skipped" in rs:
            match = "not verifiable (schema has unsupported constructs)"
        else:
            match = f"{rs['match']}/{rs['verified']}"
        lines.append(
            f"| {name} | {e['tables']} | {e['guarantees']} "
            f"({v.get('SAFE', 0)}/{v.get('CHANGED', 0)}/{v.get('BROKEN', 0)}) | {diag} | "
            f"{e['queries_translated']}/{e['queries']} | {match} |"
        )
    return "\n".join(lines)


def _agreement_block(a: dict[str, Any]) -> str:
    def row(label: str, r: dict[str, Any] | None) -> str:
        if not r:
            return f"| {label} | not measured | | | |"
        kappa = "undefined" if r["kappa"] is None else f"{r['kappa']:.2f}"
        return (
            f"| {label} | {r['pairs']} | {pct(r['rate'])} | {kappa} | {r['pairs'] - r['agreed']} |"
        )

    lines = [
        "| Advisor | Relationships | Raw agreement | Cohen's κ | Disagreements |",
        "|---|---|---|---|---|",
        row("mock (deterministic rule of thumb, **not** an AI signal)", a.get("mock")),
        row("Anthropic model", a.get("real")),
        "",
    ]
    shown = a.get("real") or a.get("mock")
    if shown and shown["disagreements"]:
        lines += [
            f"Disagreements ({shown['advisor']}), optimizer vs advisor, both rationales:",
            "",
            "| Case | Relationship | Optimizer | Advisor |",
            "|---|---|---|---|",
        ]
        for d in shown["disagreements"][:25]:
            lines.append(
                f"| {d['case']} | `{d['relationship_id']}` | **{d['optimizer']}**: {d['optimizer_reason']}"
                f" ({'; '.join(d['optimizer_factors'])}) | **{d['advisor']}** "
                f"({d['advisor_confidence']:.2f}): {d['advisor_justification']} |"
            )
        if len(shown["disagreements"]) > 25:
            lines.append(f"\n… and {len(shown['disagreements']) - 25} more in docs/metrics.json.")
    return "\n".join(lines)


def render_markdown(d: dict[str, Any]) -> str:
    sl, ind = d["self_labeled"], d["independent"]
    ext = d["external"]
    mut = d.get("mutation")
    rs_ext = ext["result_set"]
    sys_rs = d.get("result_set") or {}
    mut_text = (
        f"Mutation score {pct(mut['score'])}: {mut['killed']} of {mut['total']} mutants killed "
        f"({mut['survived']} survived, {mut.get('timeout', 0)} timed out) in {', '.join(mut['paths'])}. "
        f"Command: `{mut['command']}`. The first run scored 83.3%; tests were added for the survivors "
        "(exact reasons, scores, type map, predicate analysis, RULES.md). Mutants in code that runs at import "
        "time (the rule definitions) are not re-evaluated per mutant by mutmut's fork model, so most of the "
        "remaining survivors cannot be killed by unit tests; the exact RULES.md test covers that wording."
        if mut
        else "Not measured yet (see docs/progress/phase-15.md for the command)."
    )
    failures = sl["failures"]
    fail_text = (
        "None. Every labeled guarantee in the self-labeled corpus is predicted with the labeled status."
        if not failures
        else "\n".join(
            f"- `{f['case']}` `{f['node']}`: labeled {f['label']} ({f['label_why']}), predicted "
            f"{f['predicted']} ({f['checker_reason']})"
            for f in failures
        )
    )
    return f"""# Metrics

Computed by `schemashift corpus report` (see `docs/EVALUATION.md` for methodology).

- **Date:** {d["date"]}
- **Commit:** {d["commit"]}

The two label groups are scored **separately and never merged into one headline number**: the
self-labeled corpus is written by the author of the rules, so agreement shows internal consistency,
not independent validation. Only the independent group (labeled blind by someone else) can support
that claim, and it is currently {"empty" if not ind["schemas"] else str(len(ind["schemas"])) + " schemas"}.

| Evidence | Corpus | Result |
|---|---|---|
| Self-labeled (author) | {len(sl["schemas"])} schemas, {sl["labeled_nodes"]} guarantees | problem-detection recall {pct(sl["problem_detection"]["recall"])}, precision {pct(sl["problem_detection"]["precision"])}, F1 {pct(sl["problem_detection"]["f1"])}; severity accuracy {pct(sl["severity_accuracy"])} |
| Independent (blind, another person) | {len(ind["schemas"])} schemas | {'**pending**: needs a second labeler, see "How to add independent labels"' if not ind["schemas"] else f"problem-detection recall {pct(ind['problem_detection']['recall'])}, precision {pct(ind['problem_detection']['precision'])}"} |
| Public schemas, result-set correctness vs real PostgreSQL | {sum(1 for e in ext["schemas"].values() if "result_set" in e and "skipped" not in e["result_set"])} verifiable of {len(ext["schemas"])} | {rs_ext["match"]}/{rs_ext["verified"]} queries MATCH ({pct(rs_ext["rate"])}); needs no labels |
| Bundled samples, result-set correctness | 4 schemas | {sys_rs.get("match", "n/a")}/{sys_rs.get("verified", "n/a")} MATCH |

## Self-labeled corpus (`backend/tests/corpus/`)

{_group_block("self_labeled", sl)}
## Independent corpus (`backend/tests/corpus_independent/`)

{_group_block("independent", ind)}
### How to add independent labels
1. `schemashift corpus label-template samples/external/chinook/schema.sql --out backend/tests/corpus_independent`
   writes an empty `expected.yaml` listing every guarantee and the SQL that produced it (no verdicts).
2. Someone other than the rule author fills `status` for each node from MongoDB semantics, **without running
   SchemaShift**, sets `labeled_by` to their name and keeps `labeled_blind: true`.
3. `schemashift corpus report --write-docs` scores it in the independent group above.

## Public schemas (`samples/external/`)

Not written for SchemaShift; see each folder's README for source, license and porting notes. No labels are involved:
the diagnostics are what the parser/analyzer reports, the result sets are compared with a real PostgreSQL.

{_external_table(ext)}

Schemas with parse/analysis errors cannot be verified (the harness refuses to run them); their diagnostics are
the inventory of real-world constructs SchemaShift does not support yet.

## Optimizer vs AI advisor

{_agreement_block(d["agreement"])}

## Mutation testing

{mut_text}

## Failure cases

False positives and false negatives of the self-labeled corpus:

{fail_text}

Result-set mismatches found while building the external benchmark are listed in `docs/DECISIONS.md` (REAL arithmetic
precision, LIMIT ties, seed insertion order); none is open.

## Definitions

| Metric | Definition |
|---|---|
| Problem detection | Is a guarantee flagged (CHANGED or BROKEN) at all. TP: labeled and predicted non-SAFE; FP: labeled SAFE, predicted non-SAFE; FN: labeled non-SAFE, predicted SAFE. |
| Per-class precision / recall / F1 | One-vs-rest over SAFE, CHANGED, BROKEN, from the confusion matrix. |
| Severity accuracy | Fraction of labeled guarantees whose predicted status equals the label. |
| Cohen's κ | (observed agreement − chance agreement) / (1 − chance agreement) between optimizer and advisor over EMBED/REFERENCE. |
| Result-set correctness | MATCH queries / verified queries, PostgreSQL as the oracle. |
| Mutation score | Killed mutants / (killed + survived + timed out), mutmut on `equivalence/` and `optimizer/`. |

## Limits
Labels in the self-labeled group were written by the author of the rules. The corpus is small, schemas are mostly
synthetic, workloads are hand-written, and the real-AI agreement needs an API key (`schemashift corpus report --ai`).
"""


def write_docs(data: dict[str, Any]) -> tuple[Path, Path]:
    write_json(data)
    MD_PATH.write_text(render_markdown(data), encoding="utf-8", newline="\n")
    return MD_PATH, JSON_PATH
