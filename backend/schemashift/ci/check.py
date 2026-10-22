"""`schemashift check`: compare a schema with its baseline and apply the policy. Pure."""

from __future__ import annotations

from typing import Literal

from schemashift.ci.policy import CONSTRAINTS, Policy
from schemashift.evolve.model import ChangeOp, EvolutionPlan
from schemashift.evolve.plan import evolve
from schemashift.models.base import FrozenModel
from schemashift.models.source import SourceSpan
from schemashift.pipeline import CompileOptions, CompileResult
from schemashift.risk import score_risk

Level = Literal["error", "warning"]
REMOVALS: dict[str, tuple[str, str]] = {
    "DROP_UNIQUE": ("unique", "UNIQUE"),
    "DROP_FOREIGN_KEY": ("foreign_key", "FOREIGN KEY"),
    "DROP_CHECK": ("check", "CHECK"),
    "DROP_NOT_NULL": ("not_null", "NOT NULL"),
    "CHANGE_PRIMARY_KEY": ("primary_key", "PRIMARY KEY"),
}
POLICY_RULES: dict[str, str] = {
    "SS-REGRESSION": "A guarantee the old schema had is weaker after the change",
    "SS-REMOVED-CONSTRAINT": "A constraint of the baseline was removed",
    "SS-RISK": "The migration risk score rose or exceeds the limit",
    "SS-IRREVERSIBLE": "The change needs steps that cannot be undone automatically",
    "SS-OVERALL": "The overall verdict reached the policy's threshold",
}


class Violation(FrozenModel):
    policy_rule: str  # SS-...
    level: Level
    message: str
    equivalence_rule: str | None = None  # EQ-... rule behind it
    node_id: str | None = None
    table: str | None = None
    old: str | None = None
    new: str | None = None
    file: Literal["new", "baseline"] = "new"
    location: SourceSpan | None = None


class CheckReport(FrozenModel):
    file: str
    baseline_file: str
    violations: tuple[Violation, ...]
    risk_before: int
    risk_after: int
    overall_before: str
    overall_after: str
    changes: int
    passed: bool  # no violation of level "error"


def _constraint_text(op: ChangeOp) -> str:
    cols = op.detail.get("columns")
    if isinstance(cols, list) and cols:
        return f"{op.table}.{', '.join(cols)}"
    return f"{op.table}.{op.column}" if op.column else op.table


def _table_span(result: CompileResult, table: str) -> SourceSpan | None:
    t = result.schema_.tables.get(table)
    return t.span if t else None


def check_schemas(
    baseline_sql: str,
    new_sql: str,
    *,
    queries_sql: str = "",
    policy: Policy | None = None,
    options: CompileOptions | None = None,
    file: str = "schema.sql",
    baseline_file: str | None = None,
) -> tuple[CheckReport, EvolutionPlan, CompileResult, CompileResult]:
    policy = policy or Policy()
    plan, old, new = evolve(baseline_sql, new_sql, queries_sql=queries_sql, options=options)
    types = {
        v.node_id: v.ir_node_type
        for v in (*old.equivalence.final.verdicts, *new.equivalence.final.verdicts)
    }
    spans = {v.node_id: v.source_span for v in new.equivalence.final.verdicts}
    out: list[Violation] = []

    # -- 1. guarantees that got worse
    for c in plan.guarantees.changes:
        if c.direction != "worse" or c.old is None or c.new is None:
            continue
        key = f"{c.old}->{c.new}"
        level: Level | None = (
            "error"
            if key in policy.fail_on_regression
            else "warning"
            if key in policy.warn_on_regression
            else None
        )
        if level is None:
            continue
        what = types.get(c.node_id, c.node_id)
        cause = c.because[0] if c.because else f"{c.node_id} changed"
        out.append(
            Violation(
                policy_rule="SS-REGRESSION",
                level=level,
                message=f"{cause} -> {what} {c.old}->{c.new}, rule {c.rule_id}",
                equivalence_rule=c.rule_id,
                node_id=c.node_id,
                old=c.old,
                new=c.new,
                location=spans.get(c.node_id),
            )
        )

    # -- 2. removed constraints
    removed_by_text = {
        b: c for c in plan.guarantees.changes if c.direction == "removed" for b in c.because
    }
    for op in plan.diff.ops:
        if op.kind not in REMOVALS:
            continue
        name, label = REMOVALS[op.kind]
        gone = removed_by_text.get(op.description)
        level = "error" if name in policy.fail_on_removed else "warning"
        verb = "changed" if op.kind == "CHANGE_PRIMARY_KEY" else "removed"
        text = f"{label} {verb} on {_constraint_text(op)}"
        if gone is not None:
            what = types.get(gone.node_id, gone.node_id)
            text += f" -> {what} {gone.old}->(none), rule {gone.rule_id}"
        out.append(
            Violation(
                policy_rule="SS-REMOVED-CONSTRAINT",
                level=level,
                message=text,
                equivalence_rule=gone.rule_id if gone else None,
                node_id=gone.node_id if gone else None,
                table=op.table,
                old=gone.old if gone else None,
                new=None,
                location=_table_span(new, op.table),
            )
        )
    for op in plan.diff.ops:
        if op.kind == "DROP_TABLE":
            out.append(
                Violation(
                    policy_rule="SS-REMOVED-CONSTRAINT",
                    level="warning",
                    message=f"table {op.table} was dropped; its constraints and data go with it",
                    table=op.table,
                    file="baseline",
                    location=_table_span(old, op.table),
                )
            )

    # -- 3. risk
    r_old, r_new = score_risk(old), score_risk(new)
    if (
        policy.max_risk_increase is not None
        and r_new.overall - r_old.overall > policy.max_risk_increase
    ):
        out.append(
            Violation(
                policy_rule="SS-RISK",
                level="error",
                message=f"migration risk rose from {r_old.overall} to {r_new.overall} "
                f"(allowed increase: {policy.max_risk_increase})",
                old=str(r_old.overall),
                new=str(r_new.overall),
            )
        )
    if policy.max_risk_score is not None and r_new.overall > policy.max_risk_score:
        out.append(
            Violation(
                policy_rule="SS-RISK",
                level="error",
                message=f"migration risk {r_new.overall} exceeds the limit {policy.max_risk_score}",
                new=str(r_new.overall),
            )
        )

    # -- 4. irreversible steps
    if policy.irreversible_steps != "off":
        for s in plan.steps:
            if s.reversibility == "IRREVERSIBLE":
                out.append(
                    Violation(
                        policy_rule="SS-IRREVERSIBLE",
                        level=policy.irreversible_steps,
                        message=f"{s.description}: cannot be undone automatically ({s.reason})",
                        table=s.collection,
                    )
                )

    # -- 5. overall verdict
    rank = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}
    if (
        policy.overall_verdict_at_least != "never"
        and rank[new.overall_verdict] >= rank[policy.overall_verdict_at_least]
    ):
        out.append(
            Violation(
                policy_rule="SS-OVERALL",
                level="error",
                message=(
                    f"the overall verdict is {new.overall_verdict} "
                    f"(policy threshold: {policy.overall_verdict_at_least})"
                ),
                new=new.overall_verdict,
            )
        )
    report = CheckReport(
        file=file,
        baseline_file=baseline_file or file,
        violations=tuple(out),
        risk_before=r_old.overall,
        risk_after=r_new.overall,
        overall_before=old.overall_verdict,
        overall_after=new.overall_verdict,
        changes=len(plan.diff.ops),
        passed=not any(v.level == "error" for v in out),
    )
    return report, plan, old, new


__all__ = ["CONSTRAINTS", "POLICY_RULES", "CheckReport", "Violation", "check_schemas"]
