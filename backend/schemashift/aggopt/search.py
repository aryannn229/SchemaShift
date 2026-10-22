"""Bounded search over rewrite sequences, choosing the candidate with the lowest estimated cost."""

from __future__ import annotations

import json
from typing import Any

from schemashift.aggopt.cost import CostEstimate, Stats, estimate_cost
from schemashift.aggopt.paths import refs_in, stage_name
from schemashift.aggopt.rewrites import REWRITES, Pipeline
from schemashift.models.base import FrozenModel

MAX_CANDIDATES = 200
MAX_DEPTH = 12


class Candidate(FrozenModel):
    pipeline: list[Any]
    rewrites: tuple[str, ...]
    cost: CostEstimate


class OptimizedPipeline(FrozenModel):
    original: list[Any]
    best: Candidate
    baseline: CostEstimate
    candidates: tuple[Candidate, ...]
    index_hints: tuple[str, ...] = ()

    @property
    def applied(self) -> tuple[str, ...]:
        return self.best.rewrites

    @property
    def pipeline(self) -> list[Any]:
        return self.best.pipeline


def _key(p: Pipeline) -> str:
    return json.dumps(p, sort_keys=True, default=str)


def optimize_pipeline(
    pipeline: list[Any], collection: str | None, stats: Stats | None = None
) -> OptimizedPipeline:
    base = estimate_cost(pipeline, collection, stats)
    seen = {_key(pipeline)}
    cands: list[Candidate] = [Candidate(pipeline=pipeline, rewrites=(), cost=base)]
    frontier: list[Candidate] = list(cands)
    for _ in range(MAX_DEPTH):
        nxt: list[Candidate] = []
        for c in frontier:
            for rule in REWRITES:
                for p in rule.apply(c.pipeline):
                    k = _key(p)
                    if k in seen or len(cands) >= MAX_CANDIDATES:
                        continue
                    seen.add(k)
                    cand = Candidate(
                        pipeline=p,
                        rewrites=(*c.rewrites, rule.id),
                        cost=estimate_cost(p, collection, stats),
                    )
                    cands.append(cand)
                    nxt.append(cand)
        if not nxt:
            break
        frontier = nxt
    best = min(cands, key=lambda c: (c.cost.total, len(c.rewrites), _key(c.pipeline)))
    hints = recommend_indexes(best.pipeline, collection)
    return OptimizedPipeline(
        original=pipeline, best=best, baseline=base, candidates=tuple(cands), index_hints=hints
    )


def recommend_indexes(pipeline: list[Any], collection: str | None) -> tuple[str, ...]:
    """Indexes the pipeline can use: leading $match fields and the foreign fields of $lookups."""
    out: list[str] = []
    if pipeline and stage_name(pipeline[0]) == "$match":
        fields = sorted(r for r in refs_in(pipeline[0]["$match"]) if not r.startswith("$"))
        eq = [f for f in fields if f != "_id"]
        if eq and collection:
            out.append(f"{collection}({', '.join(eq)})")
    for s in pipeline:
        if stage_name(s) == "$lookup" and "foreignField" in s["$lookup"]:
            body = s["$lookup"]
            if body["foreignField"] != "_id":
                out.append(f"{body['from']}({body['foreignField']})")
    return tuple(dict.fromkeys(out))
