"""Ask Z3 whether the generated validator accepts exactly what the SQL constraints accept. Pure."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import z3

from schemashift.codegen.collections import CollectionSpec, build_collections
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.equivalence.checker import EquivalenceReport
from schemashift.equivalence.smt.model import SmtFinding, SmtReport, TableResult
from schemashift.equivalence.smt.mongo import Clause, MongoExpr, property_clauses
from schemashift.equivalence.smt.sql import (
    SqlEncoder,
    Unsupported,
    Var,
    make_var,
    sort_kind,
    type_constraint,
)
from schemashift.models.evidence import EvidenceCondition
from schemashift.models.schema import Column, Table
from schemashift.models.verdict import Status, Verdict, worst

if TYPE_CHECKING:  # the pipeline imports this package: avoid the cycle at runtime
    from schemashift.pipeline import CompileOptions, CompileResult

ASSUMPTIONS = (
    EvidenceCondition(
        kind="validators_installed",
        text="the generated validators are installed on the collection",
    ),
    EvidenceCondition(
        kind="null_as_absent",
        text=(
            "a SQL NULL is stored as an absent field (never as an explicit null) and "
            "values carry the column's BSON type"
        ),
    ),
)
RANK: dict[Status, int] = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}


class _SqlConstraint:
    def __init__(self, node_ids: tuple[str, ...], text: str, formula: Any) -> None:
        self.node_ids, self.text, self.formula = node_ids, text, formula


def _opaque_var(col: Column) -> Var:
    """A column whose type is outside the fragment: only its presence is modelled."""
    return Var(col.name, col.sql_type, z3.Bool(f"p_{col.name}"), z3.Int(f"o_{col.name}"))


def _fmt(model: Any, value: Any) -> str:
    v = model.eval(value, model_completion=True)
    if z3.is_int_value(v):
        return str(v.as_long())
    if z3.is_rational_value(v):
        return str(v.as_decimal(12)).rstrip("?")
    if z3.is_string_value(v):
        return str(v.as_string())
    return str(v)


def _row(model: Any, vars_: dict[str, Var], opaque: set[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name, v in vars_.items():
        if not z3.is_true(model.eval(v.present, model_completion=True)):
            out[name] = None
        else:
            out[name] = "(any value)" if name in opaque else _fmt(model, v.value)
    return out


def _table_result(
    table: Table,
    layout: TableLayout,
    spec: CollectionSpec,
    result: CompileResult,
    enums: dict[str, tuple[str, ...]],
    timeout_ms: int,
) -> TableResult:
    started = time.perf_counter()
    name, coll = table.name, spec.name
    vars_: dict[str, Var] = {}
    opaque: set[str] = set()
    for col in table.columns:
        if col.name in layout.dropped_columns:
            continue
        ev = enums.get(col.sql_type.enum_name or "", ()) if col.sql_type.base == "ENUM" else ()
        try:
            if col.sql_type.base == "FLOAT":
                raise Unsupported("single precision float")
            vars_[col.name] = make_var(col, ev)
        except Unsupported:
            vars_[col.name] = _opaque_var(col)
            opaque.add(col.name)

    enc = SqlEncoder(vars_)
    sql_parts: list[_SqlConstraint] = []
    pk = set(table.primary_key.columns) if table.primary_key else set()
    for col in table.columns:
        v = vars_.get(col.name)
        if v is None:
            continue
        nodes_type = (f"type:{name}.{col.name}",)
        if col.name not in opaque:
            try:
                tc = type_constraint(v)
            except Unsupported:
                opaque.add(
                    col.name
                )  # e.g. an enum whose values are unknown: only presence is modelled
            else:
                nodes = nodes_type + (
                    (f"enum:{name}.{col.name}",) if col.sql_type.base == "ENUM" else ()
                )
                sql_parts.append(
                    _SqlConstraint(
                        nodes, f"{col.name} has the column's type", z3.Implies(v.present, tc)
                    )
                )
        if not col.nullable or col.name in pk:
            sql_parts.append(
                _SqlConstraint(
                    (f"not_null:{name}.{col.name}",), f"{col.name} is NOT NULL", v.present
                )
            )
    for node in result.ir.nodes:
        if node.node_type != "domain_constraint" or getattr(node, "table", None) != name:
            continue
        try:
            import sqlglot

            ast = sqlglot.parse_one(node.expr_sql, dialect="postgres")
            formula = enc.check_formula(ast)
        except Unsupported as exc:
            return TableResult(
                table=name,
                collection=coll,
                status="UNSUPPORTED",
                reason=f"CHECK ({node.expr_sql}): {exc} is outside the supported fragment",
                milliseconds=int((time.perf_counter() - started) * 1000),
            )
        sql_parts.append(_SqlConstraint((node.id,), f"CHECK ({node.expr_sql})", formula))

    # ---------------------------------------------------------------- MongoDB side
    fields = {layout.fields[c]: vars_[c] for c in vars_ if c in layout.fields}
    by_field = {layout.fields[c]: c for c in vars_ if c in layout.fields}
    clauses: list[Clause] = []
    try:
        for part in spec.validator.get("$and") or [spec.validator]:
            js = part.get("$jsonSchema")
            if js is not None:
                for req in js.get("required", []):
                    if req in fields:
                        clauses.append(Clause(f"{req} is required", fields[req].present, (req,)))
                for field, sub in js.get("properties", {}).items():
                    if field not in fields:
                        continue  # embedded children: other layouts
                    if by_field[field] in opaque:
                        continue
                    clauses.extend(property_clauses(fields[field], field, sub))
            elif "$expr" in part:
                me = MongoExpr(fields)
                clauses.append(
                    Clause("an $expr validator clause", me.truth(part["$expr"]), tuple(fields))
                )
            else:
                raise Unsupported(f"validator clause {next(iter(part))}")
    except Unsupported as exc:
        return TableResult(
            table=name,
            collection=coll,
            status="UNSUPPORTED",
            reason=f"validator: {exc}",
            milliseconds=int((time.perf_counter() - started) * 1000),
        )
    implicit = [
        fields[layout.fields[c]].present for c in ([layout.id_column] if layout.id_column else [])
    ]
    background = [v.background for v in vars_.values() if v.background is not None]
    mongo_all = (
        z3.And(*[c.formula for c in clauses], *implicit)
        if clauses or implicit
        else z3.BoolVal(True)
    )
    sql_all = z3.And(*[p.formula for p in sql_parts]) if sql_parts else z3.BoolVal(True)

    def solve(formula: Any) -> tuple[str, Any]:
        s = z3.Solver()
        s.set("timeout", timeout_ms)
        s.add(*background, formula)
        r = s.check()
        return str(r), (s.model() if r == z3.sat else None)

    findings: list[SmtFinding] = []
    try:
        a_res, a_model = solve(z3.And(sql_all, z3.Not(mongo_all)))
        b_res, b_model = solve(z3.And(mongo_all, z3.Not(sql_all)))
    except z3.Z3Exception as exc:  # pragma: no cover - defensive
        return TableResult(table=name, collection=coll, status="UNSUPPORTED", reason=str(exc))
    ms = int((time.perf_counter() - started) * 1000)
    encoded = tuple(dict.fromkeys(n for p in sql_parts for n in p.node_ids))
    if "unknown" in (a_res, b_res):
        return TableResult(
            table=name,
            collection=coll,
            status="TIMEOUT",
            reason=f"no answer in {timeout_ms} ms",
            encoded=encoded,
            milliseconds=ms,
        )
    if b_model is not None:
        for p in sql_parts:  # per-constraint blame: which SQL constraint does this row violate?
            s = z3.Solver()
            s.set("timeout", timeout_ms)
            s.add(*background, mongo_all, z3.Not(p.formula))
            if s.check() == z3.sat:
                findings.append(
                    SmtFinding(
                        direction="mongo_accepts_sql_rejects",
                        row=_row(s.model(), vars_, opaque),
                        explanation=(
                            f"PostgreSQL rejects this row ({p.text}) "
                            "but the generated validator accepts it"
                        ),
                        nodes=p.node_ids,
                    )
                )
    if a_model is not None:
        failing = [
            c for c in clauses if not z3.is_true(a_model.eval(c.formula, model_completion=True))
        ]
        nodes = tuple(
            dict.fromkeys(
                n
                for c in failing
                for f in c.fields
                for n in (
                    f"type:{name}.{by_field.get(f, f)}",
                    f"not_null:{name}.{by_field.get(f, f)}",
                )
            )
        )
        findings.append(
            SmtFinding(
                direction="sql_accepts_mongo_rejects",
                row=_row(a_model, vars_, opaque),
                explanation="PostgreSQL accepts this row but the generated validator rejects it"
                + (f" ({failing[0].text})" if failing else ""),
                nodes=nodes,
                clause=failing[0].text if failing else None,
            )
        )
    return TableResult(
        table=name,
        collection=coll,
        status="COUNTEREXAMPLE" if findings else "EQUIVALENT",
        encoded=encoded,
        findings=tuple(findings),
        milliseconds=ms,
    )


def run_smt(
    result: CompileResult, options: CompileOptions | None = None, timeout_ms: int = 2000
) -> SmtReport:
    from schemashift.pipeline import CompileOptions as _Options

    options = options or _Options()
    layouts = build_layouts(result.graph, result.plan)
    specs = {
        c.name: c
        for c in build_collections(
            result.schema_, layouts, options.uuid_as, options.string_collation
        ).collections
    }
    enums = {n: e.values for n, e in result.schema_.enums.items()}
    out: list[TableResult] = []
    for table in result.schema_.tables.values():
        layout = layouts[table.name]
        if not layout.is_root:
            out.append(
                TableResult(
                    table=table.name,
                    collection=layout.collection,
                    status="UNSUPPORTED",
                    reason=(
                        f"embedded in {layout.collection}: "
                        "nested validators are outside the fragment"
                    ),
                )
            )
            continue
        out.append(
            _table_result(table, layout, specs[layout.collection], result, enums, timeout_ms)
        )
    return SmtReport(
        solver=f"z3 {z3.get_version_string()}", timeout_ms=timeout_ms, tables=tuple(out)
    )


def apply_smt(report: EquivalenceReport, smt: SmtReport) -> EquivalenceReport:
    """Verdicts after the SMT check: PROVEN where the proof covers them, status raised on a
    counterexample."""
    proven = smt.proven_nodes
    findings: dict[str, list[SmtFinding]] = {}
    for t in smt.tables:
        for f in t.findings:
            for n in f.nodes:
                findings.setdefault(n, []).append(f)
    out: list[Verdict] = []
    for v in report.verdicts:
        hits = findings.get(v.node_id)
        if hits:
            f = hits[0]
            target: Status = "BROKEN" if f.direction == "sql_accepts_mongo_rejects" else "CHANGED"
            row = ", ".join(f"{k}={'NULL' if x is None else x}" for k, x in f.row.items())
            out.append(
                v.model_copy(
                    update={
                        "status": target if RANK[target] > RANK[v.status] else v.status,
                        "reason": v.reason + f" Z3 counterexample: {f.explanation}; row ({row}).",
                        "evidence": "TESTED" if v.evidence == "UNVERIFIED" else v.evidence,
                        "decided_by": (v.decided_by + " | " if v.decided_by else "")
                        + f"smt/{f.direction}",
                    }
                )
            )
        elif v.node_id in proven and v.evidence != "PROVEN":
            out.append(
                v.model_copy(
                    update={
                        "evidence": "PROVEN",
                        "argument": (
                            f"{smt.solver}: no single row is accepted by exactly one of the "
                            "PostgreSQL constraints and the generated MongoDB validator "
                            "(both directions unsatisfiable)"
                        ),
                        "evidence_conditions": ASSUMPTIONS,
                        "decided_by": (v.decided_by + " | " if v.decided_by else "")
                        + "smt/equivalent",
                    }
                )
            )
        else:
            out.append(v)
    counts: dict[Status, int] = {"SAFE": 0, "CHANGED": 0, "BROKEN": 0}
    for v in out:
        counts[v.status] += 1
    return report.model_copy(
        update={"verdicts": tuple(out), "counts": counts, "overall": worst([v.status for v in out])}
    )


__all__ = ["apply_smt", "run_smt", "sort_kind"]
