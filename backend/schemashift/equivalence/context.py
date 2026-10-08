"""RuleContext: everything a rule may consult (graph, placement plan, target options)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.placement import PlacementPlan, junction_key
from schemashift.semantic.graph import Relationship, SchemaGraph


class RuleOptions(FrozenModel):
    target_mongo_version: str = "7.0"
    transactions_available: bool = True
    preserve_integer_ids: bool = False
    uuid_as: Literal["binary", "string"] = "binary"


@dataclass
class RuleContext:
    graph: SchemaGraph
    plan: PlacementPlan = field(default_factory=PlacementPlan)
    options: RuleOptions = field(default_factory=RuleOptions)

    # ---- placement helpers ---------------------------------------------------------
    def folded_host(self, junction: str) -> str | None:
        """Host table when a junction is folded into an array of references, else None."""
        decision = self.plan.decisions.get(junction_key(junction))
        if junction in self.graph.junctions and decision is not None:
            if decision.decision == "REF_ARRAY" and decision.host:
                return decision.host
        return None

    def is_embedded(self, rel_id: str) -> bool:
        """True when the child of this relationship lives inside its parent document."""
        if self.plan.is_embedded(rel_id):
            return True
        rel = self.graph.relationships.get(rel_id)
        return rel is not None and self.folded_host(rel.child) == rel.parent

    def embedding_relationship(self, table: str) -> Relationship | None:
        """The relationship through which ``table`` is embedded into a parent, if any."""
        for rel in self.graph.parents_of(table):
            if not rel.is_self_reference and self.is_embedded(rel.id):
                return rel
        return None

    def is_table_embedded(self, table: str) -> bool:
        return self.embedding_relationship(table) is not None

    def root_collection(self, table: str) -> str:
        """The collection a table's rows end up in (follows the embed chain upwards)."""
        seen: set[str] = set()
        current = table
        while current not in seen:
            seen.add(current)
            rel = self.embedding_relationship(current)
            if rel is None:
                return current
            current = rel.parent
        return current

    def junction_decision(self, junction: str) -> str:
        return self.plan.decision(junction_key(junction))

    # ---- cascade chains --------------------------------------------------------------
    def cascade_hops(self, rel: Relationship) -> list[Relationship]:
        """All ON DELETE CASCADE hops reachable below ``rel`` (excluding ``rel`` itself)."""
        hops: list[Relationship] = []
        seen: set[str] = {rel.id}
        frontier = [rel.child]
        while frontier:
            table = frontier.pop(0)
            for child_rel in self.graph.children_of(table):
                if child_rel.id in seen or child_rel.fk.on_delete != "CASCADE":
                    continue
                seen.add(child_rel.id)
                hops.append(child_rel)
                frontier.append(child_rel.child)
        return hops
