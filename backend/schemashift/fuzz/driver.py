"""Check one case (differential + metamorphic) and drive a hypothesis-based fuzz run."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import seed as hyp_seed
from hypothesis.errors import HypothesisException

from schemashift.fuzz import relations
from schemashift.fuzz.harness import FuzzSession, InvalidCase, SelectOutcome
from schemashift.fuzz.relations import Finding, multiset
from schemashift.fuzz.spec import FuzzCase, SelectSpec, render_select
from schemashift.fuzz.strategies import cases
from schemashift.models.base import FrozenModel
from schemashift.pipeline import CompileOptions, compile_sql
from schemashift.verification.sandbox import SandboxConfig


class CaseStats(FrozenModel):
    queries: int = 0
    compared: int = 0
    not_translated: int = 0
    pg_errors: int = 0
    relation_checks: int = 0
    relation_skipped: int = 0
    untranslated: dict[str, int] = {}


class FuzzFailure(AssertionError):
    """Raised inside the hypothesis test so the case is shrunk to a minimal reproducer."""


def _differential(outcomes: list[SelectOutcome]) -> list[Finding]:
    found: list[Finding] = []
    for o in outcomes:
        if o.status in ("MISMATCH", "MONGO_ERROR"):
            found.append(
                Finding(
                    kind="differential",
                    summary=o.hypothesis or o.detail or o.status,
                    sqls=(o.sql,),
                    detail={
                        "status": o.status,
                        "pg": [list(r) for r in o.pg[:5]],
                        "mongo": [list(r) for r in o.mongo[:5]],
                        "pipeline": o.pipeline,
                    },
                )
            )
    return found


def _first_column(spec: SelectSpec) -> str | None:
    return spec.columns[0] if spec.columns else None


def check_case(
    case: FuzzCase,
    config: SandboxConfig,
    *,
    metamorphic: bool = True,
    options: CompileOptions | None = None,
) -> tuple[list[Finding], CaseStats]:
    """Run every query on both databases and (optionally) the metamorphic relations."""
    findings: list[Finding] = []
    stats = {
        "queries": 0,
        "compared": 0,
        "not_translated": 0,
        "pg_errors": 0,
        "relation_checks": 0,
        "relation_skipped": 0,
    }

    def record(result: relations.Result) -> None:
        checked, found = result
        stats["relation_checks" if checked else "relation_skipped"] += 1
        findings.extend(found)

    with FuzzSession(case.schema_sql(), case.seed_sql(), config, options) as session:
        outcomes = [session.select(render_select(s)) for s in case.selects]
        stats["queries"] = len(outcomes)
        stats["compared"] = sum(o.comparable for o in outcomes)
        stats["not_translated"] = sum(o.status == "NOT_TRANSLATED" for o in outcomes)
        stats["pg_errors"] = sum(o.status == "PG_ERROR" for o in outcomes)
        findings.extend(_differential(outcomes))
        untranslated = session.untranslated
        if metamorphic:
            for spec, outcome in zip(case.selects, outcomes, strict=True):
                if spec.is_aggregate or not outcome.comparable:
                    continue
                if spec.where is not None and spec.where.op != "true":
                    record(relations.tlp(session, spec.with_where(None), spec.where))
                    record(relations.commute(session, spec))
                col = _first_column(spec)
                if col is not None:
                    record(relations.tautology(session, spec, col))
                record(relations.count_matches_rows(session, spec))
    if metamorphic and not findings:
        findings.extend(plan_equivalence(case, config, outcomes))
        stats["relation_checks"] += 1
    return findings, CaseStats(**stats, untranslated=untranslated)


def plan_equivalence(
    case: FuzzCase, config: SandboxConfig, reference: list[SelectOutcome]
) -> list[Finding]:
    """The same queries must give identical results under EMBED and REFERENCE plans."""
    compiled = compile_sql(case.schema_sql(), "", case.seed_sql())
    rel_ids = list(compiled.plan.decisions)
    if not rel_ids:
        return []
    found: list[Finding] = []
    for decision in ("REFERENCE", "EMBED"):
        opts = CompileOptions(relationship_overrides={r: decision for r in rel_ids})
        try:
            with FuzzSession(case.schema_sql(), case.seed_sql(), config, opts) as alt:
                for spec, base in zip(case.selects, reference, strict=True):
                    if not base.comparable:
                        continue
                    other = alt.select(render_select(spec))
                    if not other.comparable:
                        continue
                    if multiset(other.mongo) != multiset(base.mongo):
                        found.append(
                            Finding(
                                kind="plan",
                                summary=f"result differs: default plan vs all-{decision}",
                                sqls=(base.sql,),
                                detail={
                                    "default": len(base.mongo),
                                    decision.lower(): len(other.mongo),
                                },
                            )
                        )
        except InvalidCase:
            continue
    return found


# ------------------------------------------------------------------------------ driver
class RunResult(FrozenModel):
    cases: int
    seed: int
    failures: list[dict[str, Any]]
    stats: CaseStats
    duration_s: float


def case_hash(case: FuzzCase) -> str:
    return hashlib.sha1(case.model_dump_json().encode()).hexdigest()[:10]


def run_fuzz(
    config: SandboxConfig,
    *,
    cases_n: int,
    seed: int,
    metamorphic: bool = True,
    max_tables: int = 3,
    max_queries: int = 4,
    on_failure: Callable[[FuzzCase, list[Finding]], None] | None = None,
) -> RunResult:
    """Generate ``cases_n`` cases with a fixed seed; on a failure hypothesis shrinks it first."""
    totals = {
        "queries": 0,
        "compared": 0,
        "not_translated": 0,
        "pg_errors": 0,
        "relation_checks": 0,
        "relation_skipped": 0,
    }
    ran = 0
    untranslated: dict[str, int] = {}
    last_failure: list[tuple[FuzzCase, list[Finding]]] = []
    started = time.monotonic()

    @hyp_seed(seed)
    @settings(
        max_examples=cases_n,
        deadline=None,
        database=None,
        derandomize=False,
        suppress_health_check=list(HealthCheck),
        phases=[Phase.generate, Phase.shrink],
        print_blob=False,
    )
    @given(cases(max_tables=max_tables, max_queries=max_queries))
    def check(case: FuzzCase) -> None:
        nonlocal ran
        try:
            findings, stats = check_case(case, config, metamorphic=metamorphic)
        except InvalidCase:
            return  # the generated case is not valid input (not a finding)
        ran += 1
        for k, v in stats.model_dump().items():
            if k != "untranslated":
                totals[k] += v
        for reason, n in stats.untranslated.items():
            untranslated[reason] = untranslated.get(reason, 0) + n
        if findings:
            last_failure.clear()
            last_failure.append((case, findings))
            raise FuzzFailure(findings[0].summary)

    failures: list[dict[str, Any]] = []
    try:
        check()
    except (FuzzFailure, HypothesisException):
        pass
    if last_failure:
        case, findings = last_failure[-1]  # hypothesis replays the minimal example last
        failures.append({"hash": case_hash(case), "case": case, "findings": findings})
        if on_failure:
            on_failure(case, findings)
    return RunResult(
        cases=ran,
        seed=seed,
        failures=[
            {"hash": f["hash"], "findings": [x.model_dump() for x in f["findings"]]}
            for f in failures
        ],
        stats=CaseStats(**totals, untranslated=untranslated),
        duration_s=round(time.monotonic() - started, 1),
    )
