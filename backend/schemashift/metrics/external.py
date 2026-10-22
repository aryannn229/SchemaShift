"""Benchmark on public schemas (samples/external): diagnostics inventory and result-set correctness.

These schemas were not written for SchemaShift. Two things are measured on them without any
hand-written labels, so the numbers do not depend on the rule author's opinions:

* the inventory of constructs the parser/analyzer reports (what real schemas contain that we
  reject);
* result-set correctness of the hand-written workload against a real PostgreSQL (the oracle),
  for schemas that compile without errors.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from schemashift.codegen import generate_code
from schemashift.pipeline import CompileOptions, compile_sql

SAMPLES = Path(__file__).resolve().parents[3] / "samples"
EXTERNAL = SAMPLES / "external"


def external_names(root: Path = EXTERNAL) -> list[str]:
    return (
        sorted(p.name for p in root.iterdir() if (p / "schema.sql").exists())
        if root.exists()
        else []
    )


def evaluate_external(root: Path = EXTERNAL, sandbox: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    matched = verified = 0
    for name in external_names(root):
        folder = root / name
        schema = (folder / "schema.sql").read_text("utf-8")
        queries_file = folder / "queries.sql"
        queries = queries_file.read_text("utf-8") if queries_file.exists() else ""
        options = CompileOptions()
        result = compile_sql(schema, queries, "", options)
        code = generate_code(result, options)
        counts = result.equivalence.final.counts
        entry: dict[str, Any] = {
            "tables": len(result.schema_.tables),
            "guarantees": sum(counts.values()),
            "verdicts": dict(counts),
            "diagnostics": dict(
                sorted(Counter(d.code for d in result.diagnostics).items(), key=lambda kv: -kv[1])
            ),
            "has_errors": result.has_errors,
            "queries": len(code.queries),
            "queries_translated": sum(1 for q in code.queries if not q.error),
            "not_translated": {q.query_id: q.error for q in code.queries if q.error},
        }
        if sandbox is not None and not result.has_errors:
            from schemashift.verification import verify

            report = verify(result, options, sandbox, seed=1, run_probes=False)
            done = report.verified
            ok = sum(o.status == "MATCH" for o in done)
            matched += ok
            verified += len(done)
            entry["result_set"] = {
                "match": ok,
                "verified": len(done),
                "mismatches": [
                    {"query": o.query_id, "sql": o.sql, "hypothesis": o.hypothesis}
                    for o in done
                    if o.status != "MATCH"
                ],
                "notes": [{"query": o.query_id, "note": o.note} for o in done if o.note],
            }
        elif sandbox is not None:
            entry["result_set"] = {"skipped": "the schema has parse/analysis errors"}
        out[name] = entry
    return {
        "schemas": out,
        "result_set": {
            "computed": sandbox is not None,
            "match": matched,
            "verified": verified,
            "rate": matched / verified if verified else None,
        },
    }
