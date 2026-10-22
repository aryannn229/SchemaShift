"""Save minimal reproducers as YAML and replay them forever.

A regression stores the rendered SQL (schema, seed, queries), not the generator's data model, so it
keeps working however the strategies evolve. ``replay`` re-runs the differential comparison and, for
metamorphic findings, the violated relation.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from schemashift.fuzz.driver import case_hash
from schemashift.fuzz.harness import FuzzSession, InvalidCase
from schemashift.fuzz.relations import Finding, multiset
from schemashift.fuzz.spec import FuzzCase
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification.sandbox import SandboxConfig

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "tests" / "regressions"


def entry_for(case: FuzzCase, findings: list[Finding], seed: int) -> dict[str, Any]:
    """The YAML document for a minimal failing case."""
    first = findings[0]
    return {
        "id": f"fuzz_{case_hash(case)}",
        "found_on": dt.date.today().isoformat(),
        "fuzz_seed": seed,
        "violated": first.kind,
        "summary": first.summary,
        "schema_sql": case.schema_sql(),
        "seed_sql": case.seed_sql(),
        "queries": list(first.sqls) if first.kind != "differential" else list(first.sqls),
        "plan": "default" if first.kind != "plan" else "compare-default-vs-reference-and-embed",
        "observed": first.detail,
        "status": "open",
    }


def save(entry: dict[str, Any], directory: Path = DEFAULT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{entry['id']}.yaml"
    path.write_text(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True), encoding="utf8")
    return path


def load_all(directory: Path = DEFAULT_DIR) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    return [
        yaml.safe_load(p.read_text(encoding="utf8")) for p in sorted(directory.glob("fuzz_*.yaml"))
    ]


def replay(entry: dict[str, Any], config: SandboxConfig) -> list[str]:
    """Re-run a saved regression; returns the problems still present (empty = fixed)."""
    problems: list[str] = []
    schema, seed_sql, queries = entry["schema_sql"], entry["seed_sql"], entry["queries"]
    try:
        with FuzzSession(schema, seed_sql, config) as s:
            outs = [s.select(q) for q in queries]
            for o in outs:
                if o.status in ("MISMATCH", "MONGO_ERROR"):
                    problems.append(f"differential: {o.sql}: {o.hypothesis or o.detail}")
            kind = entry["violated"]
            if kind == "tlp" and len(outs) == 4 and all(o.comparable for o in outs):
                whole, a, b, c = (multiset(o.mongo) for o in outs)
                if a + b + c != whole:
                    problems.append("tlp: partitions do not add up")
            elif (
                kind in ("tautology", "commute")
                and len(outs) == 2
                and all(o.comparable for o in outs)
            ):
                if multiset(outs[0].mongo) != multiset(outs[1].mongo):
                    problems.append(f"{kind}: results differ")
            elif kind == "count" and len(outs) == 2 and all(o.comparable for o in outs):
                if outs[1].mongo and int(outs[1].mongo[0][0]) != len(outs[0].mongo):
                    problems.append("count: COUNT(*) differs from the row count")
    except InvalidCase as exc:
        problems.append(f"case no longer valid: {exc}")
    if entry["violated"] == "plan":
        problems.extend(_replay_plan(entry, config))
    return problems


def _replay_plan(entry: dict[str, Any], config: SandboxConfig) -> list[str]:
    schema, seed_sql, queries = entry["schema_sql"], entry["seed_sql"], entry["queries"]
    rel_ids = list(compile_sql(schema, "", seed_sql).plan.decisions)
    problems: list[str] = []
    with FuzzSession(schema, seed_sql, config) as base:
        reference = [base.select(q) for q in queries]
    for decision in ("REFERENCE", "EMBED"):
        opts = CompileOptions(relationship_overrides={r: decision for r in rel_ids})
        with FuzzSession(schema, seed_sql, config, opts) as alt:
            for q, ref in zip(queries, reference, strict=True):
                other = alt.select(q)
                if (
                    ref.comparable
                    and other.comparable
                    and multiset(ref.mongo) != multiset(other.mongo)
                ):
                    problems.append(f"plan: {q} differs under all-{decision}")
    return problems
