"""Check the pipeline cost model against MongoDB: ``explain("executionStats")`` of every candidate.

For each SELECT the optimizer's candidates (original, every rewrite sequence reached by the bounded
search) are explained on the loaded sandbox. Observed cost is what MongoDB reports it did:
documents examined plus the documents every stage handed on. The report gives the Spearman rank
correlation between the estimate and that observation, pooled over all candidates and per query,
and whether the candidate the optimizer picked was among the cheapest observed.
"""

from __future__ import annotations

import json
from typing import Any

from pymongo.errors import PyMongoError

from schemashift.aggopt import Stats, optimize_pipeline
from schemashift.models.base import FrozenModel

MAX_CANDIDATES_EXPLAINED = 12


class ExplainedCandidate(FrozenModel):
    rewrites: tuple[str, ...]
    estimated: float
    observed: float | None
    docs_examined: int | None = None
    error: str | None = None


class ExplainedQuery(FrozenModel):
    query_id: str
    chosen: tuple[str, ...]
    chosen_estimated: float
    baseline_estimated: float
    baseline_observed: float | None
    chosen_observed: float | None
    candidates: list[ExplainedCandidate]
    rho: float | None = None  # within this query; None with fewer than 3 distinct observations
    chosen_is_cheapest: bool | None = None


class PipelineExplain(FrozenModel):
    queries: list[ExplainedQuery]
    rho: float | None  # Spearman over every (query, candidate) pair
    points: int
    chosen_cheapest: int  # queries whose chosen candidate has the lowest observed cost (ties count)
    explained_queries: int


def _walk(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            else:
                found.extend(_walk(v, key))
    elif isinstance(node, list):
        for v in node:
            found.extend(_walk(v, key))
    return found


def observed_cost(plan: dict[str, Any]) -> tuple[float, int]:
    """(cost, documents examined) from an explain document."""
    examined = sum(int(x) for x in _walk(plan, "totalDocsExamined") if isinstance(x, int))
    keys = sum(int(x) for x in _walk(plan, "totalKeysExamined") if isinstance(x, int))
    stages = plan.get("stages")
    if isinstance(stages, list):
        returned = sum(
            int(s["nReturned"]) for s in stages if isinstance(s, dict) and "nReturned" in s
        )
    else:
        returned = sum(int(x) for x in _walk(plan.get("executionStats", {}), "nReturned")[:1])
    return float(examined + keys * 0.1 + returned), examined


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (average ranks for ties); None when undefined."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return round(cov / (vx * vy) ** 0.5, 4) if vx and vy else None


def stats_for(db: Any, indexed: dict[str, set[str]] | None = None) -> dict[str, int]:
    return {name: db[name].estimated_document_count() for name in db.list_collection_names()}


def explain_one(db: Any, collection: str, pipeline: list[Any]) -> tuple[float, int]:
    from schemashift.codegen.values import materialize

    cmd = {
        "aggregate": collection,
        "pipeline": materialize(pipeline),
        "cursor": {},
    }
    plan = db.command({"explain": cmd, "verbosity": "executionStats"})
    return observed_cost(json.loads(json.dumps(plan, default=str)))


def explain_pipelines(db: Any, queries: list[Any], indexed: dict[str, set[str]]) -> PipelineExplain:
    """``queries`` are translated SELECTs (pipeline + collection); ``db`` is the loaded sandbox."""
    counts = stats_for(db)
    out: list[ExplainedQuery] = []
    all_est: list[float] = []
    all_obs: list[float] = []
    cheapest = 0
    for tq in queries:
        if not tq.pipeline or not tq.collection:
            continue
        base_pipeline = tq.original_pipeline or tq.pipeline
        other = max(counts.values(), default=1)
        stats = Stats(
            docs=float(max(1, counts.get(tq.collection, 1))),
            other_docs=float(max(1, other)),
            indexed=indexed,
        )
        opt = optimize_pipeline(base_pipeline, tq.collection, stats)
        cands: list[ExplainedCandidate] = []
        for c in list(opt.candidates)[:MAX_CANDIDATES_EXPLAINED]:
            try:
                obs, examined = explain_one(db, tq.collection, c.pipeline)
                cands.append(
                    ExplainedCandidate(
                        rewrites=c.rewrites,
                        estimated=c.cost.total,
                        observed=obs,
                        docs_examined=examined,
                    )
                )
            except PyMongoError as exc:
                cands.append(
                    ExplainedCandidate(
                        rewrites=c.rewrites,
                        estimated=c.cost.total,
                        observed=None,
                        error=str(exc).splitlines()[0],
                    )
                )
        good = [c for c in cands if c.observed is not None]
        est = [c.estimated for c in good]
        obs_list = [c.observed or 0.0 for c in good]
        all_est += est
        all_obs += obs_list
        base_c = next((c for c in cands if not c.rewrites), None)
        chosen_c = next((c for c in cands if c.rewrites == opt.applied), None)
        is_cheapest = None
        if chosen_c is not None and chosen_c.observed is not None and good:
            is_cheapest = chosen_c.observed <= min(obs_list) + 1e-9
            cheapest += int(is_cheapest)
        out.append(
            ExplainedQuery(
                query_id=tq.query_id,
                chosen=opt.applied,
                chosen_estimated=opt.best.cost.total,
                baseline_estimated=opt.baseline.total,
                baseline_observed=base_c.observed if base_c else None,
                chosen_observed=chosen_c.observed if chosen_c else None,
                candidates=cands,
                rho=spearman(est, obs_list),
                chosen_is_cheapest=is_cheapest,
            )
        )
    return PipelineExplain(
        queries=out,
        rho=spearman(all_est, all_obs),
        points=len(all_est),
        chosen_cheapest=cheapest,
        explained_queries=len(out),
    )
