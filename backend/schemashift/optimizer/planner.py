"""Global placement planner: per-edge scores -> a consistent PlacementPlan.

Rules enforced here (the cost model scores edges independently):
- a table is embedded in at most one parent (multi-parent conflicts keep the best edge);
- embedding cycles are broken by referencing the lowest-scoring edge;
- nested embedding depth is limited (default 3);
- a root document must stay under the size ceiling once nested children are included;
- pure junction tables folded into one side become an array of references (REF_ARRAY).
"""

from __future__ import annotations

from collections.abc import Iterable

from schemashift.ir.nodes import IRProgram
from schemashift.models.base import FrozenModel
from schemashift.models.placement import (
    Factor,
    Placement,
    PlacementDecision,
    PlacementPlan,
    junction_key,
)
from schemashift.models.query import Query, TransactionBlock
from schemashift.optimizer.cost_model import CostResult, score_relationship
from schemashift.optimizer.features import (
    RelationshipFeatures,
    estimate_doc_bytes,
    extract_features,
)
from schemashift.optimizer.weights import Weights, load_weights
from schemashift.optimizer.workload import AccessHint
from schemashift.semantic.graph import SchemaGraph


class OptimizerOptions(FrozenModel):
    access_hints: dict[str, AccessHint] = {}
    relationship_overrides: dict[str, Placement] = {}
    weights: Weights | None = None


class _State:
    """Mutable working state of one planning run."""

    def __init__(
        self,
        graph: SchemaGraph,
        feats: dict[str, RelationshipFeatures],
        costs: dict[str, CostResult],
        w: Weights,
    ) -> None:
        self.graph = graph
        self.feats = feats
        self.costs = costs
        self.w = w
        self.chosen: set[str] = set()  # embedded relationship ids
        self.reasons: dict[str, str] = {rid: c.reason for rid, c in costs.items()}
        self.forced: set[str] = set()
        self.warnings: list[str] = []

    # ---- helpers ---------------------------------------------------------------
    def child_of(self, rid: str) -> str:
        return self.feats[rid].child

    def parent_of(self, rid: str) -> str:
        return self.feats[rid].parent

    def edge_by_child(self) -> dict[str, str]:
        return {self.child_of(rid): rid for rid in self.chosen}

    def drop(self, rid: str, reason: str) -> None:
        self.chosen.discard(rid)
        self.reasons[rid] = reason
        if rid in self.forced:
            self.warnings.append(f"override EMBED on {rid} was not applied: {reason}")

    def weakest(self, rids: Iterable[str]) -> str:
        return min(rids, key=lambda r: (r in self.forced, self.costs[r].score, r))

    def depth(self, table: str) -> int:
        edges = self.edge_by_child()
        depth, seen = 0, {table}
        while table in edges:
            table = self.parent_of(edges[table])
            if table in seen:
                break
            seen.add(table)
            depth += 1
        return depth

    def doc_bytes(self, table: str) -> int:
        return estimate_doc_bytes(self.graph.schema.tables[table])

    def size(self, table: str, seen: frozenset[str] = frozenset()) -> float:
        total = float(self.doc_bytes(table))
        for rid in sorted(self.chosen):
            if self.parent_of(rid) == table and self.child_of(rid) not in seen | {table}:
                f = self.feats[rid]
                total += (
                    f.estimated_child_count_per_parent
                    * self.size(f.child, seen | {table})
                    * self.w.hard.safety_factor
                )
        return total


def plan_placement(
    graph: SchemaGraph,
    program: IRProgram,
    queries: Iterable[Query | TransactionBlock] = (),
    seed_queries: Iterable[Query | TransactionBlock] = (),
    options: OptimizerOptions | None = None,
) -> PlacementPlan:
    options = options or OptimizerOptions()
    w = options.weights or load_weights()
    feats = extract_features(graph, program, queries, seed_queries, options.access_hints, w)
    costs = {rid: score_relationship(f, w) for rid, f in feats.items()}
    st = _State(graph, feats, costs, w)

    candidates = _apply_overrides(st, options.relationship_overrides)
    _resolve_multi_parent(st, candidates)
    _break_cycles(st)
    _limit_depth(st)
    _limit_size(st)
    junction_decisions = _place_junctions(st, options.relationship_overrides)
    return _build_plan(st, options.relationship_overrides, junction_decisions)


# --------------------------------------------------------------------------- steps
def _apply_overrides(st: _State, overrides: dict[str, Placement]) -> set[str]:
    candidates: set[str] = set()
    for rid, cost in st.costs.items():
        ov = overrides.get(rid)
        if ov == "EMBED":
            if st.feats[rid].cardinality == "self":
                st.warnings.append(
                    f"override EMBED on {rid} ignored: self-references cannot be embedded"
                )
                st.reasons[rid] = "self-references cannot be embedded"
                continue
            st.forced.add(rid)
            candidates.add(rid)
            st.reasons[rid] = "user override: EMBED"
        elif ov is not None:
            st.reasons[rid] = f"user override: {ov}"
        elif cost.decision == "EMBED":
            candidates.add(rid)
    for key in overrides:
        if key not in st.costs and not key.startswith("m2n:"):
            st.warnings.append(f"override for unknown relationship {key!r} ignored")
    return candidates


def _resolve_multi_parent(st: _State, candidates: set[str]) -> None:
    """A table is embedded in at most one parent: keep the best candidate edge."""
    by_child: dict[str, list[str]] = {}
    for rid in sorted(candidates):
        by_child.setdefault(st.child_of(rid), []).append(rid)
    for child, rids in by_child.items():
        rids.sort(key=lambda r: (r not in st.forced, -st.costs[r].score, r))
        winner, *losers = rids
        st.chosen.add(winner)
        for loser in losers:
            st.drop(
                loser,
                f"{child} is embedded in {st.parent_of(winner)} instead (at most one parent)",
            )


def _break_cycles(st: _State) -> None:
    while True:
        cycle = _find_cycle(st)
        if cycle is None:
            return
        victim = st.weakest(cycle)
        st.drop(victim, "embedding cycle: lowest-scoring edge of the cycle is referenced")


def _find_cycle(st: _State) -> list[str] | None:
    edges = st.edge_by_child()
    for start in sorted(edges):
        path: list[str] = []
        table = start
        while table in edges and table not in path:
            path.append(table)
            table = st.parent_of(edges[table])
        if table in path:
            return [edges[t] for t in path[path.index(table) :]]
    return None


def _limit_depth(st: _State) -> None:
    limit = st.w.hard.max_embed_depth
    while True:
        edges = st.edge_by_child()
        too_deep = sorted(
            (t for t in edges if st.depth(t) > limit), key=lambda t: (-st.depth(t), t)
        )
        if not too_deep:
            return
        st.drop(edges[too_deep[0]], f"nested embedding deeper than {limit} levels")


def _limit_size(st: _State) -> None:
    ceiling = st.w.hard.max_parent_doc_bytes
    while True:
        child_tables = {st.child_of(r) for r in st.chosen}
        offenders = [
            t
            for t in sorted(st.graph.schema.tables)
            if t not in child_tables and st.size(t) > ceiling
        ]
        if not offenders:
            return
        root = offenders[0]
        subtree = _subtree_edges(st, root)
        if not subtree:
            return
        victim = max(
            subtree,
            key=lambda r: (
                st.feats[r].estimated_child_count_per_parent * st.size(st.child_of(r)),
                r,
            ),
        )
        st.drop(victim, f"projected {root} document exceeds {ceiling // 1_048_576} MB")


def _subtree_edges(st: _State, root: str) -> list[str]:
    found: list[str] = []
    frontier, seen = [root], {root}
    while frontier:
        table = frontier.pop()
        for rid in sorted(st.chosen):
            if st.parent_of(rid) == table and st.child_of(rid) not in seen:
                found.append(rid)
                seen.add(st.child_of(rid))
                frontier.append(st.child_of(rid))
    return found


def _place_junctions(st: _State, overrides: dict[str, Placement]) -> dict[str, PlacementDecision]:
    """Decide the M:N placement of every junction table."""
    out: dict[str, PlacementDecision] = {}
    for j in st.graph.junctions.values():
        key = junction_key(j.table)
        edge_ids = [r.id for r in st.graph.parents_of(j.table) if r.id in st.feats]
        embedded = sorted(
            (r for r in edge_ids if r in st.chosen), key=lambda r: (-st.costs[r].score, r)
        )
        ov = overrides.get(key)
        fold_host: str | None = None
        reason = "junction kept as its own collection"
        if ov == "REF_ARRAY":
            fold_host = _lower_fanout_parent(st, edge_ids, embedded)
            reason = "user override: REF_ARRAY"
        elif ov == "EMBED":
            st.warnings.append(f"override EMBED on {key} ignored: M:N is never fully embedded")
        elif ov == "REFERENCE":
            reason = "user override: REFERENCE"
        elif embedded and not j.payload_columns:
            fold_host = st.parent_of(embedded[0])
            reason = f"pure junction folded into an array of references on {fold_host}"
        if fold_host is not None:
            for rid in embedded:
                st.drop(rid, f"junction {j.table} is folded into an array of references")
            score = max((st.costs[r].score for r in edge_ids), default=0.0)
            out[key] = PlacementDecision(
                relationship_id=key,
                decision="REF_ARRAY",
                score=score,
                reason=reason,
                overridden=ov == "REF_ARRAY",
                host=fold_host,
            )
        else:
            out[key] = PlacementDecision(
                relationship_id=key,
                decision="REFERENCE",
                reason=reason,
                overridden=ov == "REFERENCE",
            )
    return out


def _lower_fanout_parent(st: _State, edge_ids: list[str], embedded: list[str]) -> str:
    if embedded:
        return st.parent_of(embedded[0])
    best = min(edge_ids, key=lambda r: (st.feats[r].estimated_child_count_per_parent, r))
    return st.parent_of(best)


def _build_plan(
    st: _State, overrides: dict[str, Placement], junctions: dict[str, PlacementDecision]
) -> PlacementPlan:
    decisions: dict[str, PlacementDecision] = {}
    for rid in sorted(st.costs):
        embedded = rid in st.chosen
        cost = st.costs[rid]
        decisions[rid] = PlacementDecision(
            relationship_id=rid,
            decision="EMBED" if embedded else "REFERENCE",
            score=cost.score,
            top_factors=tuple(
                Factor(name=f.name, contribution=f.contribution) for f in cost.factors[:4]
            ),
            reason=st.reasons[rid] if not embedded or rid in st.forced else cost.reason,
            overridden=rid in overrides,
            host=st.parent_of(rid) if embedded else None,
        )
    decisions.update(junctions)
    return PlacementPlan(decisions=decisions, warnings=tuple(st.warnings))
