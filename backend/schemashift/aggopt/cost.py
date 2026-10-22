"""Estimated cost of a pipeline: documents scanned, $lookup work and intermediate document volume.

The numbers are heuristics, deliberately simple and documented: the point is the ranking of
candidate
pipelines, which ``explain.py`` checks against MongoDB's own execution statistics (Spearman rho).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log2
from typing import Any

from schemashift.aggopt.paths import Stage, stage_name
from schemashift.models.base import FrozenModel

BASE_WIDTH = 8.0  # fields in a stored document
LOOKUP_WIDTH = 6.0  # fields a $lookup adds
FANOUT = 3.0  # matches per joined row
EQ_SELECTIVITY, RANGE_SELECTIVITY, OTHER_SELECTIVITY = 0.1, 0.33, 0.5


class CostEstimate(FrozenModel):
    docs_scanned: float
    lookups: int
    lookup_work: float
    intermediate: float  # sum over stages of (documents in x width)
    total: float


@dataclass
class Stats:
    """What the estimate knows: collection sizes and the indexed fields per collection."""

    docs: float = 1000.0
    other_docs: float = 1000.0
    indexed: dict[str, set[str]] = field(default_factory=dict)  # collection -> indexed fields


def selectivity(match: Any) -> float:
    if not isinstance(match, dict):
        return OTHER_SELECTIVITY
    s = 1.0
    for k, v in match.items():
        if k == "$and" and isinstance(v, list):
            for sub in v:
                s *= selectivity(sub)
        elif k == "$or" and isinstance(v, list):
            s *= min(1.0, sum(selectivity(sub) for sub in v))
        elif k.startswith("$"):
            s *= OTHER_SELECTIVITY
        elif isinstance(v, dict) and any(op in v for op in ("$gt", "$gte", "$lt", "$lte")):
            s *= RANGE_SELECTIVITY
        elif isinstance(v, dict) and "$in" in v:
            s *= min(1.0, EQ_SELECTIVITY * max(1, len(v["$in"])))
        elif isinstance(v, dict):
            s *= OTHER_SELECTIVITY
        else:
            s *= EQ_SELECTIVITY
    return max(s, 0.001)


def _indexed_prefix(match: Any, indexed: set[str]) -> bool:
    return isinstance(match, dict) and any(
        k in indexed or k == "_id" for k in match if not k.startswith("$")
    )


def estimate_cost(
    pipeline: list[Stage], collection: str | None, stats: Stats | None = None
) -> CostEstimate:
    st = stats or Stats()
    idx = st.indexed.get(collection or "", set())
    docs, width = st.docs, BASE_WIDTH
    scanned = docs
    if pipeline and stage_name(pipeline[0]) == "$match":
        m = pipeline[0]["$match"]
        if _indexed_prefix(m, idx):
            scanned = max(1.0, docs * selectivity(m))
    lookups, lookup_work, intermediate, handoffs = 0, 0.0, 0.0, 0.0
    for stage in pipeline:
        name = stage_name(stage)
        body = stage[name]
        if name == "$match":
            docs = max(1.0, docs * selectivity(body))
        elif name == "$lookup":
            lookups += 1
            foreign = st.indexed.get(body.get("from", ""), set()) | {"_id"}
            indexed = body.get("foreignField") in foreign
            per = (log2(st.other_docs) + 1) if indexed else st.other_docs / 10
            lookup_work += docs * per
            width += LOOKUP_WIDTH
        elif name == "$unwind":
            preserve = isinstance(body, dict) and body.get("preserveNullAndEmptyArrays")
            docs = docs * FANOUT * (1.0 if preserve else 0.8)
        elif name == "$group":
            intermediate += docs * width  # the group reads every incoming document in full
            docs = max(1.0, docs * 0.1)
            width = float(len(body))
        elif name == "$project":
            kept = [k for k, v in body.items() if v and k != "_id"]
            if kept and all(v in (1, True) for k, v in body.items() if k != "_id"):
                width = min(width, float(len(kept)))
        elif name in ("$addFields", "$set"):
            width += len(body)
        elif name == "$limit":
            docs = min(docs, float(body))
        elif name == "$skip":
            docs = max(1.0, docs - float(body))
        intermediate += docs * width  # size of what this stage hands to the next one
        handoffs += docs  # documents this stage returns (MongoDB reports it as nReturned)
    total = scanned + lookup_work + handoffs + 0.25 * intermediate / BASE_WIDTH
    return CostEstimate(
        docs_scanned=round(scanned, 2),
        lookups=lookups,
        lookup_work=round(lookup_work, 2),
        intermediate=round(intermediate, 2),
        total=round(total, 2),
    )
