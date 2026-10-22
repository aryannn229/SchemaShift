"""Decision simulator (Phase 24): what happens if the user overrides the optimizer? Pure.

The optimizer's own plan is never modified. A simulation compiles the schema twice, once as the
optimizer decides and once with the user's overrides, workload frequencies and constraint changes,
and
reports the difference: placements (with the overrides that were blocked and why), verdicts and
evidence,
indexes, validators, pipelines with estimated cost, and the risk score.
"""

from __future__ import annotations

from typing import Any

from schemashift.codegen import CodegenResult, generate_code
from schemashift.codegen.indexes import build_indexes
from schemashift.codegen.layout import build_layouts
from schemashift.evolve.alter import apply_alters
from schemashift.evolve.differ import diff_schemas
from schemashift.evolve.plan import diff_guarantees, diff_plans
from schemashift.models.base import FrozenModel
from schemashift.models.placement import Placement
from schemashift.optimizer.workload import WorkloadSpec
from schemashift.pipeline import CompileOptions, CompileResult, compile_schema, compile_sql
from schemashift.risk import RiskReport, score_risk


class PlacementRow(FrozenModel):
    relationship_id: str
    optimizer: Placement | None
    simulated: Placement | None
    overridden: bool
    blocked: bool = False
    blocked_reason: str | None = None


class VerdictDelta(FrozenModel):
    node_id: str
    rule_id: str
    before: str | None
    after: str | None
    evidence_before: str | None
    evidence_after: str | None
    because: tuple[str, ...] = ()


class IndexDelta(FrozenModel):
    added: tuple[str, ...]
    removed: tuple[str, ...]


class PipelineDelta(FrozenModel):
    query_id: str
    collection_before: str | None
    collection_after: str | None
    changed: bool
    estimated_cost_before: float | None
    estimated_cost_after: float | None
    rewrites: tuple[str, ...] = ()


class RiskDelta(FrozenModel):
    before: int
    after: int
    level_before: str
    level_after: str
    categories: dict[str, tuple[int, int]]


class Simulation(FrozenModel):
    label: str = "simulated plan (your overrides, not the optimizer's output)"
    placements: tuple[PlacementRow, ...]
    blocked: tuple[PlacementRow, ...]
    verdicts: tuple[VerdictDelta, ...]
    overall_before: str
    overall_after: str
    indexes: IndexDelta
    validators_changed: tuple[str, ...]
    pipelines: tuple[PipelineDelta, ...]
    risk: RiskDelta
    risk_after: RiskReport
    schema_changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _index_names(result: CompileResult, layouts: Any) -> set[str]:
    return {
        f"{i.collection}({', '.join(k for k, _ in i.keys)}){' unique' if i.unique else ''}"
        for i in build_indexes(result.schema_, result.graph, layouts)
    }


def _risk(result: CompileResult, code: CodegenResult) -> RiskReport:
    return score_risk(
        result,
        [q.query_id for q in code.queries if q.error],
        len(code.untranslated_checks),
    )


def simulate(
    schema_sql: str,
    queries_sql: str = "",
    *,
    options: CompileOptions | None = None,
    overrides: dict[str, Placement] | None = None,
    query_freq: dict[str, float] | None = None,
    alter_sql: str = "",
) -> Simulation:
    base_opts = options or CompileOptions()
    # the optimizer's own plan: the user's overrides in the options are not applied to the baseline
    baseline_opts = base_opts.model_copy(update={"relationship_overrides": {}})
    sim_overrides = {**base_opts.relationship_overrides, **(overrides or {})}
    workload = (
        WorkloadSpec(
            query_freq={**base_opts.workload.query_freq, **(query_freq or {})},
            default_freq=base_opts.workload.default_freq,
        )
        if query_freq
        else base_opts.workload
    )
    sim_opts = base_opts.model_copy(
        update={"relationship_overrides": sim_overrides, "workload": workload}
    )
    before = compile_sql(schema_sql, queries_sql, "", baseline_opts)
    warnings: list[str] = []
    changes: tuple[str, ...] = ()
    ops: tuple[Any, ...] = ()
    if alter_sql.strip():
        schema2, warns, renames = apply_alters(before.schema_, alter_sql)
        warnings += warns
        after = compile_schema(schema2, queries_sql, "", sim_opts)
        diff = diff_schemas(before.schema_, after.schema_, renames)
        ops = diff.ops
        changes = tuple(o.description for o in ops)
    else:
        after = compile_sql(schema_sql, queries_sql, "", sim_opts)
    code_b, code_a = generate_code(before, baseline_opts), generate_code(after, sim_opts)
    lay_b, lay_a = build_layouts(before.graph, before.plan), build_layouts(after.graph, after.plan)

    rows: list[PlacementRow] = []
    blocked: list[PlacementRow] = []
    for rid in sorted(set(before.plan.decisions) | set(after.plan.decisions)):
        b, a = before.plan.decisions.get(rid), after.plan.decisions.get(rid)
        wanted = sim_overrides.get(rid)
        was_blocked = wanted is not None and a is not None and a.decision != wanted
        reason = None
        if was_blocked:
            reason = next((w for w in after.plan.warnings if rid in w), None) or (
                f"a hard constraint forces {a.decision if a else 'another placement'}"
            )
        row = PlacementRow(
            relationship_id=rid,
            optimizer=b.decision if b else None,
            simulated=a.decision if a else None,
            overridden=wanted is not None,
            blocked=was_blocked,
            blocked_reason=reason,
        )
        rows.append(row)
        if was_blocked:
            blocked.append(row)

    plan_changes = diff_plans(before, after)
    guarantees = diff_guarantees(before, after, ops, plan_changes)
    ev_b = {v.node_id: v.evidence for v in before.equivalence.final.verdicts}
    ev_a = {v.node_id: v.evidence for v in after.equivalence.final.verdicts}
    verdicts = tuple(
        VerdictDelta(
            node_id=c.node_id,
            rule_id=c.rule_id,
            before=c.old,
            after=c.new,
            evidence_before=ev_b.get(c.node_id),
            evidence_after=ev_a.get(c.node_id),
            because=c.because,
        )
        for c in guarantees.changes
    )
    # evidence-only changes (same status, different level) are verdict changes too
    seen = {v.node_id for v in verdicts}
    status_b = {v.node_id: v for v in before.equivalence.final.verdicts}
    extra = tuple(
        VerdictDelta(
            node_id=v.node_id,
            rule_id=v.rule_id,
            before=status_b[v.node_id].status,
            after=v.status,
            evidence_before=status_b[v.node_id].evidence,
            evidence_after=v.evidence,
        )
        for v in after.equivalence.final.verdicts
        if v.node_id in status_b
        and v.node_id not in seen
        and status_b[v.node_id].evidence != v.evidence
    )
    ib, ia = _index_names(before, lay_b), _index_names(after, lay_a)

    from schemashift.codegen.collections import build_collections

    cb = {c.name: c for c in build_collections(before.schema_, lay_b).collections}
    ca = {c.name: c for c in build_collections(after.schema_, lay_a).collections}
    validators = tuple(
        sorted(
            n
            for n in set(cb) | set(ca)
            if n not in cb or n not in ca or cb[n].validator != ca[n].validator
        )
    )

    qb = {q.query_id: q for q in code_b.queries}
    qa = {q.query_id: q for q in code_a.queries}
    pipes = tuple(
        PipelineDelta(
            query_id=qid,
            collection_before=qb[qid].collection if qid in qb else None,
            collection_after=qa[qid].collection if qid in qa else None,
            changed=(qb.get(qid) and qb[qid].pipeline) != (qa.get(qid) and qa[qid].pipeline)
            or (qb.get(qid) and qb[qid].operations) != (qa.get(qid) and qa[qid].operations),
            estimated_cost_before=(qb[qid].cost_after or qb[qid].cost_before)
            if qid in qb
            else None,
            estimated_cost_after=(qa[qid].cost_after or qa[qid].cost_before) if qid in qa else None,
            rewrites=tuple(qa[qid].rewrites) if qid in qa else (),
        )
        for qid in sorted(set(qb) | set(qa))
    )
    rb, ra = _risk(before, code_b), _risk(after, code_a)
    return Simulation(
        placements=tuple(rows),
        blocked=tuple(blocked),
        verdicts=verdicts + extra,
        overall_before=before.overall_verdict,
        overall_after=after.overall_verdict,
        indexes=IndexDelta(added=tuple(sorted(ia - ib)), removed=tuple(sorted(ib - ia))),
        validators_changed=validators,
        pipelines=pipes,
        risk=RiskDelta(
            before=rb.overall,
            after=ra.overall,
            level_before=rb.level,
            level_after=ra.level,
            categories={
                c.id: (c.score, next(x.score for x in ra.categories if x.id == c.id))
                for c in rb.categories
            },
        ),
        risk_after=ra,
        schema_changes=changes,
        warnings=tuple(warnings) + tuple(after.plan.warnings),
    )
