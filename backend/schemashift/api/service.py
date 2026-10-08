"""Glue between the pure compiler pipeline and the API DTOs / database."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from schemashift.api.schemas import (
    CompileRequest,
    CompileResponse,
    GraphEdge,
    GraphNode,
    QueryTranslation,
    UntranslatedCheckDTO,
)
from schemashift.api.settings import Settings
from schemashift.codegen import (
    CodegenResult,
    MongoshEmitter,
    PymongoEmitter,
    generate_code,
    make_header,
)
from schemashift.codegen.layout import build_layouts
from schemashift.models.query import TransactionBlock
from schemashift.models.source import Diagnostic
from schemashift.pipeline import CompileResult, compile_sql


class RequestTooLarge(ValueError):
    """The input exceeds a documented limit (tables, queries)."""


def check_limits(result: CompileResult, settings: Settings) -> None:
    if len(result.schema_.tables) > settings.max_tables:
        raise RequestTooLarge(f"at most {settings.max_tables} tables are supported")
    flat = sum(len(q.statements) if isinstance(q, TransactionBlock) else 1 for q in result.queries)
    if flat > settings.max_queries:
        raise RequestTooLarge(f"at most {settings.max_queries} queries are supported")


def run_compile(
    req: CompileRequest, settings: Settings, advisor: Any, cache: Any
) -> tuple[CompileResult, CodegenResult]:
    result = compile_sql(req.schema_sql, req.queries_sql, req.seed_sql, req.options, advisor, cache)
    check_limits(result, settings)
    return result, generate_code(result, req.options)


def build_response(
    result: CompileResult, codegen: CodegenResult, ai_enabled: bool
) -> CompileResponse:
    final = result.equivalence.final
    layouts = build_layouts(result.graph, result.plan)
    header = make_header("preview", "1970-01-01T00:00:00Z")
    decisions = result.plan.decisions
    nodes = [
        GraphNode(
            id=name,
            columns=len(t.columns),
            kind=layouts[name].kind,
            collection=layouts[name].collection,
        )
        for name, t in result.schema_.tables.items()
    ]
    edges = [
        GraphEdge(
            id=rid,
            child=rel.child,
            parent=rel.parent,
            cardinality=str(rel.cardinality),
            columns=list(rel.fk.columns),
            on_delete=rel.fk.on_delete,
            decision=result.plan.decision(rid),
            self_reference=rel.is_self_reference,
        )
        for rid, rel in result.graph.relationships.items()
    ]
    queries = [
        QueryTranslation(
            query_id=q.query_id,
            sql=q.sql,
            kind=str(q.kind),
            collection=q.collection,
            output_columns=q.output_columns,
            error=q.error,
            notes=q.notes,
            mongosh=MongoshEmitter().queries([q], header).content,
            python=PymongoEmitter().queries([q], header).content,
            transaction_id=q.transaction_id,
        )
        for q in codegen.queries
    ]
    return CompileResponse(
        overall_verdict=final.overall,
        counts={k: int(v) for k, v in final.counts.items()},
        has_errors=result.has_errors,
        diagnostics=list(result.diagnostics),
        queries_line_offset=result.queries_line_offset,
        ir_text=result.ir_text,
        ir_node_count=len(result.ir.nodes),
        verdicts=list(final.verdicts),
        initial_counts={k: int(v) for k, v in result.equivalence.initial.counts.items()},
        changed_by_placement=list(result.equivalence.changed_by_placement),
        plan=sorted(decisions.values(), key=lambda d: d.relationship_id),
        plan_warnings=list(result.plan.warnings),
        graph_nodes=nodes,
        graph_edges=edges,
        ai_enabled=ai_enabled,
        generated=list(codegen.files),
        queries=queries,
        untranslated_checks=[
            UntranslatedCheckDTO(table=u.table, name=u.name, sql=u.sql, fragment=u.fragment)
            for u in codegen.untranslated_checks
        ],
        warnings=list(codegen.warnings),
    )


def diagnostics_summary(diags: list[Diagnostic]) -> dict[str, int]:
    out: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    for d in diags:
        out[d.severity] += 1
    return out


# ------------------------------------------------------------------------- export
def report_markdown(run_id: str, detail: dict[str, Any]) -> str:
    result = detail.get("result") or {}
    lines = [
        f"# SchemaShift report {run_id}",
        "",
        f"- Overall verdict: **{result.get('overall_verdict', 'n/a')}**",
        f"- Counts: {result.get('counts')}",
        "",
        "## Diagnostics",
        "",
    ]
    diags = result.get("diagnostics") or []
    lines += [f"- {d['severity']} {d['code']}: {d['message']}" for d in diags] or ["- none"]
    lines += ["", "## Verdicts", "", "| Status | Node | Rule | Reason |", "|---|---|---|---|"]
    for v in result.get("verdicts") or []:
        reason = str(v["reason"]).replace("|", "\\|")
        lines.append(f"| {v['status']} | `{v['node_id']}` | {v['rule_id']} | {reason} |")
    lines += ["", "## Placement plan", ""]
    for d in result.get("plan") or []:
        lines.append(
            f"- `{d['relationship_id']}`: {d['decision']} (score {d['score']:.2f}) - {d['reason']}"
        )
    verification = detail.get("verification")
    if verification:
        lines += ["", "## Verification", ""]
        for q in verification.get("queries") or []:
            lines.append(f"- {q['query_id']} {q['status']}: {q['sql']}")
        for p in verification.get("probes") or []:
            lines.append(f"- probe {p['verdict_id']}: {p['description']} -> {p['mongo']}")
    return "\n".join(lines) + "\n"


def export_zip(run_id: str, detail: dict[str, Any]) -> bytes:
    """Generated code + report.json + report.md in one archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in (detail.get("result") or {}).get("generated") or []:
            zf.writestr(f"code/{f['path']}", f["content"])
        zf.writestr("report.json", json.dumps(detail, indent=2, default=str))
        zf.writestr("report.md", report_markdown(run_id, detail))
        zf.writestr("schema.sql", detail.get("schema_sql", ""))
        if detail.get("queries_sql"):
            zf.writestr("queries.sql", detail["queries_sql"])
    return buf.getvalue()
