"""`schemashift evaluate --all`: reproduce every reported number with one command (Phase 27).

Each section is independent: when a prerequisite is missing (no databases, no Docker) the section
records why it was skipped instead of silently reusing an old number. The result is written to
``docs/evaluation.json`` and rendered into the generated block of ``docs/EVALUATION.md``.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from schemashift.metrics import report as metrics_report
from schemashift.pipeline import CompileOptions, compile_sql

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"
BEGIN, END = "<!-- BEGIN GENERATED RESULTS -->", "<!-- END GENERATED RESULTS -->"
SAMPLES = ("ecommerce", "blog", "banking", "university")


def _skip(reason: str) -> dict[str, Any]:
    return {"computed": False, "skipped": reason}


# ------------------------------------------------------------------------------ environment
def _memory_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [  # noqa: RUF012 - ctypes layout
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_ext", ctypes.c_ulonglong),
                ]

            st = Status()
            st.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return float(round(st.total_phys / 2**30, 1))
        return float(round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1))
    except Exception:  # noqa: BLE001 - hardware information is best effort
        return None


def environment(seeds: dict[str, int], sandbox: Any) -> dict[str, Any]:
    import hypothesis
    import psycopg
    import pymongo
    import z3

    env: dict[str, Any] = {
        "date": date.today().isoformat(),
        "commit": metrics_report.git_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "versions": {
            "sqlglot": importlib.metadata.version("sqlglot"),
            "z3": z3.get_version_string(),
            "hypothesis": hypothesis.__version__,
            "pymongo": pymongo.version,
            "psycopg": psycopg.__version__,
        },
        "seeds": seeds,
    }
    if sandbox is not None:
        try:
            with psycopg.connect(sandbox.pg_admin_dsn) as conn:
                row = conn.execute("SELECT version()").fetchone()
                env["postgres"] = str(row[0] if row else "").split(",")[0]
            client: Any = pymongo.MongoClient(sandbox.mongo_url, serverSelectionTimeoutMS=3000)
            env["mongodb"] = client.server_info().get("version")
            client.close()
        except Exception as exc:  # noqa: BLE001
            env["databases"] = f"unreachable: {exc}"
    return env


# ------------------------------------------------------------------------------ sections
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _schemas() -> list[tuple[str, str, str]]:
    """(name, schema, queries) for the corpus, the bundled samples and the public schemas."""
    out: list[tuple[str, str, str]] = []
    corpus = REPO / "backend" / "tests" / "corpus"
    folders = [(f"corpus/{d.name}", d) for d in sorted(corpus.iterdir()) if d.is_dir()]
    for base in (REPO / "samples", REPO / "samples" / "external"):
        folders += [(f"samples/{d.name}", d) for d in sorted(base.iterdir()) if d.is_dir()]
    for name, d in folders:
        if (d / "schema.sql").exists():
            out.append((name, _read(d / "schema.sql"), _read(d / "queries.sql")))
    return out


def evidence_section() -> dict[str, Any]:
    """Proven / conditional / tested / unverified, with and without the SMT upgrade."""
    off: Counter[str] = Counter()
    on: Counter[str] = Counter()
    smt: Counter[str] = Counter()
    schemas = 0
    for _name, schema, queries in _schemas():
        try:
            base = compile_sql(schema, queries, "", CompileOptions(smt=False, sensitivity=False))
            full = compile_sql(schema, queries, "", CompileOptions(sensitivity=False))
        except Exception:  # noqa: BLE001 - a schema the pipeline rejects is not evidence
            continue
        schemas += 1
        off.update(v.evidence for v in base.equivalence.final.verdicts)
        on.update(v.evidence for v in full.equivalence.final.verdicts)
        if full.smt:
            smt.update(t.status for t in full.smt.tables)
    return {
        "computed": True,
        "schemas": schemas,
        "without_smt": dict(off),
        "with_smt": dict(on),
        "smt_table_outcomes": dict(smt),
    }


def smt_soundness_section(sandbox: Any, examples: int) -> dict[str, Any]:
    if sandbox is None:
        return _skip("needs PostgreSQL and MongoDB (SANDBOX_ADMIN_DATABASE_URL, MONGO_URL)")
    env = {
        **os.environ,
        "SMT_EXAMPLES": str(examples),
        "TEST_PG_DSN": sandbox.pg_admin_dsn,
        "TEST_MONGO_URL": sandbox.mongo_url,
        "PYTHONIOENCODING": "utf-8",
    }
    test = REPO / "backend" / "tests" / "integration" / "test_smt_soundness.py"
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "integration", "-s", str(test)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO / "backend",
        timeout=3600,
    )
    line = next((x for x in proc.stdout.splitlines() if "smt soundness:" in x), "")
    stats: dict[str, Any] = {}
    if line:
        try:
            stats = yaml.safe_load(line.split("smt soundness:", 1)[1].strip()) or {}
        except yaml.YAMLError:
            stats = {}
    return {
        "computed": bool(stats),
        "examples": examples,
        "passed": proc.returncode == 0,
        "stats": stats,
        "seconds": round(time.monotonic() - started, 1),
        "note": "hypothesis (derandomized); counterexample rows are inserted into both databases",
    }


def fuzz_section(sandbox: Any, cases: int, seed: int) -> dict[str, Any]:
    if sandbox is None:
        return _skip("needs PostgreSQL and MongoDB")
    from schemashift.fuzz.driver import run_fuzz

    started = time.monotonic()
    res = run_fuzz(sandbox, cases_n=cases, seed=seed, metamorphic=True)
    files = (REPO / "backend" / "tests" / "regressions").glob("fuzz_*.yaml")
    status = Counter(yaml.safe_load(p.read_text("utf-8")).get("status", "?") for p in files)
    return {
        "computed": True,
        "cases": res.cases,
        "seed": seed,
        "queries": res.stats.queries,
        "compared": res.stats.compared,
        "relation_checks": res.stats.relation_checks,
        "optimizer_checked": res.stats.optimizer_checked,
        "failures": len(res.failures),
        "seconds": round(time.monotonic() - started, 1),
        "bugs_found_to_date": {"fixed": status.get("fixed", 0), "open": status.get("open", 0)},
    }


def verification_section(sandbox: Any) -> dict[str, Any]:
    """Result-set correctness, concurrency probes and the pipeline cost model on the samples."""
    if sandbox is None:
        return _skip("needs PostgreSQL and MongoDB")
    from schemashift.verification import verify
    from schemashift.verification.explain import spearman

    per: dict[str, Any] = {}
    est: list[float] = []
    obs: list[float] = []
    for name in SAMPLES:
        d = REPO / "samples" / name
        result = compile_sql(_read(d / "schema.sql"), _read(d / "queries.sql"))
        rep = verify(result, CompileOptions(rows_per_table=200), sandbox, concurrency_iterations=30)
        pe = rep.pipeline_explain
        conc = {"probes": 0, "generated_violations": 0, "unguarded_violations": 0}
        for probe in rep.concurrency:
            conc["probes"] += 1
            key = (
                "generated_violations" if probe.strategy == "generated" else "unguarded_violations"
            )
            conc[key] += probe.violations
        per[name] = {
            "queries": len(rep.verified),
            "match": sum(o.status == "MATCH" for o in rep.queries),
            "correctness": round(rep.correctness, 4),
            "unexplained": len(rep.unexplained),
            "pipeline_rho": pe.rho if pe else None,
            "pipeline_points": pe.points if pe else 0,
            "chosen_cheapest": f"{pe.chosen_cheapest}/{pe.explained_queries}" if pe else None,
            "concurrency": conc,
        }
        for q in pe.queries if pe else []:
            for cand in q.candidates:
                if cand.observed is not None:
                    est.append(cand.estimated)
                    obs.append(cand.observed)
    return {
        "computed": True,
        "samples": per,
        "pooled_pipeline_rho": spearman(est, obs),
        "pooled_points": len(est),
    }


def bench_section(sandbox: Any, scale: int, reps: int) -> dict[str, Any]:
    if sandbox is None:
        return _skip("needs PostgreSQL and MongoDB")
    from schemashift.bench import run_bench

    config = sandbox.model_copy(
        update={"statement_timeout_ms": 600_000, "idle_in_transaction_timeout_ms": 600_000}
    )
    out: dict[str, Any] = {}
    won = total = 0
    for name in ("ecommerce", "blog"):
        d = REPO / "samples" / name
        rep = run_bench(
            _read(d / "schema.sql"),
            _read(d / "queries.sql"),
            config,
            scale=scale,
            warmups=2,
            reps=reps,
        )
        wins = [f for f in rep.findings if "chosen plan is the fastest" in f]
        losses = [f for f in rep.findings if "beat the chosen plan" in f]
        won += len(wins)
        total += len(wins) + len(losses)
        out[name] = {
            "scale_rows_per_table": scale,
            "correctness": {p.name: p.correctness for p in rep.plans},
            "chosen_fastest": len(wins),
            "chosen_not_fastest": len(losses),
            "findings": rep.findings,
        }
    return {"computed": True, "samples": out, "chosen_fastest_overall": f"{won}/{total}"}


# ------------------------------------------------------------------------------ driver
def run_all(
    sandbox: Any = None,
    *,
    fuzz_cases: int = 100,
    fuzz_seed: int = 52,
    smt_examples: int = 200,
    bench_scale: int = 1000,
    bench_reps: int = 5,
    skip: frozenset[str] = frozenset(),
    log: Callable[[str], Any] = print,
) -> dict[str, Any]:
    seeds = {"fuzz": fuzz_seed, "smt": 0, "bench": 0, "verification": 0}
    data: dict[str, Any] = {"environment": environment(seeds, sandbox)}

    def mutation() -> dict[str, Any]:
        return metrics_report.load_mutation() or _skip("run scripts/mutation.sh (needs Docker)")

    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("corpus", lambda: metrics_report.compute(sandbox=sandbox)),
        ("evidence", evidence_section),
        ("smt_soundness", lambda: smt_soundness_section(sandbox, smt_examples)),
        ("fuzz", lambda: fuzz_section(sandbox, fuzz_cases, fuzz_seed)),
        ("verification", lambda: verification_section(sandbox)),
        ("bench", lambda: bench_section(sandbox, bench_scale, bench_reps)),
        ("mutation", mutation),
    ]
    for name, fn in steps:
        if name in skip:
            data[name] = _skip("skipped by --skip")
            continue
        log(f"evaluating {name} ...")
        started = time.monotonic()
        try:
            data[name] = fn()
        except Exception as exc:  # noqa: BLE001 - one failing section must not hide the others
            data[name] = _skip(f"failed: {type(exc).__name__}: {str(exc)[:200]}")
        log(f"  {name}: {time.monotonic() - started:.1f}s")
    return data


# ------------------------------------------------------------------------------ rendering
def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _skipped(section: dict[str, Any]) -> str:
    return f"Skipped: {section.get('skipped')}"


def render(data: dict[str, Any]) -> str:
    env = data["environment"]
    libs = ", ".join(f"{k} {v}" for k, v in env["versions"].items())
    seeds = ", ".join(f"{k}={v}" for k, v in env["seeds"].items())
    lines = [
        f"Generated by `schemashift evaluate --all` on {env['date']} (commit {env['commit']}).",
        "",
        "### Environment",
        f"Python {env['python']} on {env['platform']}; {env['cpu_count']} logical CPUs, "
        f"{env['memory_gb']} GB RAM. PostgreSQL: {env.get('postgres', 'not used')}. "
        f"MongoDB: {env.get('mongodb', 'not used')}. Libraries: {libs}. Seeds: {seeds}.",
        "",
    ]
    corpus = data.get("corpus", {})
    if corpus.get("equivalence"):
        e = corpus["equivalence"]
        independent = len(corpus.get("independent", {}).get("schemas", []))
        kappa = (corpus.get("agreement", {}).get("mock") or {}).get("kappa")
        lines += [
            "### Checker accuracy (self-labeled corpus)",
            f"{e['corpus_schemas']} schemas, {e['labeled_nodes']} labeled guarantees: detection "
            f"{_pct(e['detection_rate'])}, false positives {_pct(e['false_positive_rate'])}, "
            f"severity accuracy {_pct(e['severity_accuracy'])}. The labels were written by the "
            "author of the "
            "rules, so this checks consistency, not independent correctness. Independent (blind, "
            f"second labeler) schemas: {independent}. Optimizer vs mock advisor kappa: {kappa}.",
            "",
        ]
    ev = data.get("evidence", {})
    if ev.get("computed"):
        lines += [
            "### Evidence levels",
            f"Verdicts over {ev['schemas']} schemas. Without the SMT step: {ev['without_smt']}. "
            f"With it: {ev['with_smt']}. SMT outcome per table: {ev['smt_table_outcomes']}.",
            "",
        ]
    s = data.get("smt_soundness", {})
    detail = (
        f"{s['examples']} random schemas in {s['seconds']} s: {s['stats']}. Passed: {s['passed']}."
        if s.get("computed")
        else _skipped(s)
    )
    lines += ["### Z3 soundness against the real databases", detail, ""]
    f = data.get("fuzz", {})
    detail = (
        f"{f['cases']} cases, seed {f['seed']}: {f['queries']} queries compared, "
        f"{f['relation_checks']} metamorphic checks, {f['optimizer_checked']} optimizer checks, "
        f"{f['failures']} failure(s) in {f['seconds']} s. Bugs found to date: "
        f"{f['bugs_found_to_date']}."
        if f.get("computed")
        else _skipped(f)
    )
    lines += ["### Differential and metamorphic fuzzing", detail, ""]
    v = data.get("verification", {})
    lines.append("### Result-set correctness, concurrency probes, pipeline cost model")
    if v.get("computed"):
        for name, row in v["samples"].items():
            lines.append(
                f"- **{name}**: {row['match']}/{row['queries']} queries match PostgreSQL "
                f"({_pct(row['correctness'])}); pipeline rho {row['pipeline_rho']} over "
                f"{row['pipeline_points']} candidates; optimizer's pick cheapest in "
                f"{row['chosen_cheapest']}; concurrency {row['concurrency']}."
            )
        pooled = f"{v['pooled_pipeline_rho']} over {v['pooled_points']} pairs"
        lines.append(f"- Pooled pipeline-cost rho: {pooled}.")
    else:
        lines.append(_skipped(v))
    lines += ["", "### Benchmarks (chosen plan vs all-REFERENCE vs all-EMBED)"]
    b = data.get("bench", {})
    if b.get("computed"):
        for name, row in b["samples"].items():
            n = row["chosen_fastest"] + row["chosen_not_fastest"]
            lines.append(
                f"- **{name}** at {row['scale_rows_per_table']} rows/table: chosen plan fastest on "
                f"{row['chosen_fastest']} of {n} timed queries; correctness {row['correctness']}."
            )
        lines.append(f"- Overall: chosen plan fastest on {b['chosen_fastest_overall']}.")
    else:
        lines.append(_skipped(b))
    m = data.get("mutation", {})
    lines += ["", "### Mutation score", _skipped(m) if m.get("computed") is False else str(m)]
    return "\n".join(lines)


def summary(data: dict[str, Any]) -> dict[str, Any]:
    """The few numbers the About page shows (merged into docs/metrics.json)."""
    v = data.get("verification", {})
    b = data.get("bench", {})
    return {
        "smt_soundness": data.get("smt_soundness", {}),
        "fuzz": data.get("fuzz", {}),
        "pooled_pipeline_rho": v.get("pooled_pipeline_rho"),
        "pooled_points": v.get("pooled_points"),
        "bench": {"chosen_fastest_overall": b.get("chosen_fastest_overall")},
        "evidence": data.get("evidence", {}),
    }


def write_outputs(data: dict[str, Any]) -> list[Path]:
    metrics_path = DOCS / "metrics.json"
    if metrics_path.exists():
        merged = json.loads(metrics_path.read_text(encoding="utf-8"))
        merged["evaluation"] = summary(data)
        text = json.dumps(merged, indent=2, default=str) + "\n"
        metrics_path.write_text(text, encoding="utf-8", newline="\n")
    js = DOCS / "evaluation.json"
    js.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    md = DOCS / "EVALUATION.md"
    text = md.read_text(encoding="utf-8")
    block = f"{BEGIN}\n{render(data)}\n{END}"
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        text = head + block + tail
    else:
        text = text.rstrip("\n") + "\n\n## Results\n\n" + block + "\n"
    md.write_text(text, encoding="utf-8", newline="\n")
    return [js, md]
