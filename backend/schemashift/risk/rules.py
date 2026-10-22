"""The risk rules. Each rule is data: an id, a category, a title and a function returning
findings."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.models.verdict import Verdict
from schemashift.pipeline import CompileResult
from schemashift.risk.model import Level, RiskCategory, RiskFinding, RiskReport

# (id, label, weight, reference points: the amount of findings that we call severe)
CATEGORIES: tuple[tuple[str, str, float, float], ...] = (
    ("constraint_loss", "Constraint loss", 0.30, 120.0),
    ("query_incompatibility", "Query incompatibility", 0.25, 20.0),
    ("migration_complexity", "Migration complexity", 0.15, 30.0),
    ("runtime_enforcement", "Runtime enforcement", 0.15, 100.0),
    ("concurrency", "Concurrency risk", 0.15, 25.0),
)
GENERIC_CONDITIONS = {
    "validators_installed",
    "indexes_created",
}  # true of every verdict: not a finding
LEVELS: tuple[tuple[int, Level], ...] = ((75, "severe"), (50, "high"), (25, "moderate"), (0, "low"))
QUERY_NODE_TYPES = {"JoinSemantics", "AggregateSemantics"}
FORMULA = (
    "category points = sum of the points of its findings; category score = "
    "min(100, round(100 x points / reference)) with reference 120 / 20 / 30 / 100 / 25 points; "
    "overall = round(0.30 constraint loss + 0.25 query incompatibility + "
    "0.15 migration complexity "
    "+ 0.15 runtime enforcement + 0.15 concurrency); "
    "level: <25 low, <50 moderate, <75 high, else severe."
)


@dataclass(frozen=True)
class RiskContext:
    result: CompileResult
    layouts: dict[str, TableLayout]
    untranslated_queries: Sequence[str] = ()  # ids of queries that could not be translated
    untranslated_checks: int = 0  # CHECK constraints no validator can express
    verdicts: tuple[Verdict, ...] = field(init=False)
    concurrency: tuple[Verdict, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdicts", self.result.equivalence.final.verdicts)
        object.__setattr__(self, "concurrency", self.result.concurrency.verdicts)


@dataclass(frozen=True)
class RiskRule:
    id: str
    category: str
    title: str
    check: Callable[[RiskContext], list[RiskFinding]]


def _f(rule: str, cat: str, title: str, points: float, detail: str, *links: str) -> RiskFinding:
    return RiskFinding(
        rule_id=rule, category=cat, title=title, points=points, detail=detail, links=tuple(links)
    )


# --------------------------------------------------------------------------- constraint loss
def _loss(ctx: RiskContext) -> list[RiskFinding]:
    out = []
    for v in ctx.verdicts:
        if v.status == "SAFE" or v.ir_node_type in QUERY_NODE_TYPES:
            continue
        if v.status == "BROKEN":
            out.append(
                _f("RISK-LOSS-BROKEN", "constraint_loss", "Guarantee lost", 6, v.reason, v.node_id)
            )
        else:
            out.append(
                _f(
                    "RISK-LOSS-CHANGED",
                    "constraint_loss",
                    "Guarantee changed",
                    2,
                    v.reason,
                    v.node_id,
                )
            )
    return out


def _loss_unverified(ctx: RiskContext) -> list[RiskFinding]:
    return [
        _f(
            "RISK-LOSS-UNVERIFIED",
            "constraint_loss",
            "Changed guarantee without evidence",
            1,
            f"{v.rule_id} is {v.status} and its evidence level is UNVERIFIED",
            v.node_id,
        )
        for v in ctx.verdicts
        if v.status != "SAFE"
        and v.evidence == "UNVERIFIED"
        and v.ir_node_type not in QUERY_NODE_TYPES
    ]


# --------------------------------------------------------------------------- query incompatibility
def _query_verdicts(ctx: RiskContext) -> list[RiskFinding]:
    out = []
    for v in ctx.verdicts:
        if v.ir_node_type not in QUERY_NODE_TYPES or v.status == "SAFE":
            continue
        pts = 6 if v.status == "BROKEN" else 2
        out.append(
            _f(
                "RISK-QUERY-VERDICT",
                "query_incompatibility",
                f"Query semantics {v.status}",
                pts,
                v.reason,
                v.node_id,
            )
        )
    return out


def _query_untranslated(ctx: RiskContext) -> list[RiskFinding]:
    return [
        _f(
            "RISK-QUERY-UNTRANSLATED",
            "query_incompatibility",
            "Query cannot be translated",
            8,
            f"{q} has no MongoDB equivalent in the supported subset",
            q,
        )
        for q in ctx.untranslated_queries
    ]


# --------------------------------------------------------------------------- migration complexity
def _complexity(ctx: RiskContext) -> list[RiskFinding]:
    out = []
    for lay in ctx.layouts.values():
        if lay.kind in ("array", "object"):
            out.append(
                _f(
                    "RISK-MIG-EMBEDDED",
                    "migration_complexity",
                    "Table embedded into another document",
                    2,
                    f"{lay.table} is nested in {lay.host}: its rows are regrouped under the parent",
                    lay.relationship_id or lay.table,
                )
            )
        elif lay.kind in ("ref_scalars", "ref_docs"):
            out.append(
                _f(
                    "RISK-MIG-FOLDED",
                    "migration_complexity",
                    "Junction table folded into an array",
                    1,
                    f"{lay.table} becomes an array on {lay.host}",
                    lay.relationship_id or lay.table,
                )
            )
    for t in ctx.result.schema_.tables.values():
        if t.primary_key is None:
            out.append(
                _f(
                    "RISK-MIG-NO-KEY",
                    "migration_complexity",
                    "Table without primary key",
                    2,
                    f"{t.name} has no key: documents get synthetic ids",
                    t.name,
                )
            )
    for rid, d in ctx.result.plan.decisions.items():
        if d.fragility is not None and d.fragility.level == "fragile":
            out.append(
                _f(
                    "RISK-MIG-FRAGILE",
                    "migration_complexity",
                    "Placement decision is fragile",
                    1,
                    f"{rid} flips under a 10% change of one weight",
                    rid,
                )
            )
    for rel in ctx.result.graph.relationships.values():
        if rel.is_self_reference:
            out.append(
                _f(
                    "RISK-MIG-SELF-REF",
                    "migration_complexity",
                    "Self-referencing relationship",
                    1,
                    f"{rel.id} needs $graphLookup or application traversal",
                    rel.id,
                )
            )
    if ctx.untranslated_checks:
        out.append(
            _f(
                "RISK-MIG-CHECK",
                "migration_complexity",
                "CHECK constraints no validator can express",
                3 * ctx.untranslated_checks,
                f"{ctx.untranslated_checks} CHECK constraint(s) need application code",
            )
        )
    return out


# --------------------------------------------------------------------------- runtime enforcement
def _enforcement(ctx: RiskContext) -> list[RiskFinding]:
    out = []
    for v in ctx.verdicts:
        if v.status == "SAFE" and v.evidence != "CONDITIONAL":
            continue
        if v.mitigation:
            out.append(
                _f(
                    "RISK-RT-MITIGATION",
                    "runtime_enforcement",
                    "Needs application-level enforcement",
                    2,
                    v.mitigation,
                    v.node_id,
                )
            )
        elif v.evidence == "CONDITIONAL" and any(
            c.kind not in GENERIC_CONDITIONS for c in v.evidence_conditions
        ):
            out.append(
                _f(
                    "RISK-RT-CONDITIONAL",
                    "runtime_enforcement",
                    "Holds only while a condition is kept true",
                    1,
                    "; ".join(
                        c.text for c in v.evidence_conditions if c.kind not in GENERIC_CONDITIONS
                    )[:200],
                    v.node_id,
                )
            )
    return out


# --------------------------------------------------------------------------- concurrency
def _concurrency(ctx: RiskContext) -> list[RiskFinding]:
    out = []
    for v in ctx.concurrency:
        if v.status == "SAFE":
            continue
        pts = 8 if v.status == "BROKEN" else 3
        out.append(
            _f(
                "RISK-CONC-VERDICT",
                "concurrency",
                f"Race condition {v.status}",
                pts,
                v.reason,
                v.node_id,
            )
        )
    return out


RISK_RULES: tuple[RiskRule, ...] = (
    RiskRule("RISK-LOSS", "constraint_loss", "BROKEN/CHANGED constraint verdicts", _loss),
    RiskRule(
        "RISK-LOSS-UNVERIFIED", "constraint_loss", "Changed without evidence", _loss_unverified
    ),
    RiskRule(
        "RISK-QUERY-VERDICT",
        "query_incompatibility",
        "BROKEN/CHANGED query verdicts",
        _query_verdicts,
    ),
    RiskRule(
        "RISK-QUERY-UNTRANSLATED",
        "query_incompatibility",
        "Untranslatable queries",
        _query_untranslated,
    ),
    RiskRule("RISK-MIG", "migration_complexity", "Restructuring and data movement", _complexity),
    RiskRule("RISK-RT", "runtime_enforcement", "Application-level enforcement", _enforcement),
    RiskRule("RISK-CONC", "concurrency", "Concurrency scenarios", _concurrency),
)


def level_of(score: int) -> Level:
    return next(name for floor, name in LEVELS if score >= floor)


def score_risk(
    result: CompileResult,
    untranslated_queries: Sequence[str] = (),
    untranslated_checks: int = 0,
) -> RiskReport:
    """The risk report for a compile result. Deterministic; every point traces to a finding."""
    layouts = build_layouts(result.graph, result.plan)
    ctx = RiskContext(result, layouts, tuple(untranslated_queries), untranslated_checks)
    findings = [f for rule in RISK_RULES for f in rule.check(ctx)]
    cats: list[RiskCategory] = []
    for cid, label, weight, reference in CATEGORIES:
        mine = tuple(f for f in findings if f.category == cid)
        points = sum(f.points for f in mine)
        cats.append(
            RiskCategory(
                id=cid,
                label=label,
                weight=weight,
                reference=reference,
                points=points,
                score=min(100, round(100 * points / reference)),
                findings=mine,
            )
        )
    overall = round(sum(c.weight * c.score for c in cats))
    return RiskReport(
        overall=overall, level=level_of(overall), categories=tuple(cats), formula=FORMULA
    )
