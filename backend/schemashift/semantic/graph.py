"""Relationship graph: nodes are tables, edges are foreign keys (child -> parent)."""

from __future__ import annotations

import networkx as nx

from schemashift.models.base import FrozenModel
from schemashift.models.schema import ForeignKey, Schema
from schemashift.semantic.cardinality import (
    Cardinality,
    JunctionTable,
    detect_junction,
    infer_cardinality,
    is_self_reference,
)


def fk_id(table: str, fk: ForeignKey) -> str:
    """Stable identifier of a foreign key, e.g. ``fk:orders.customer_id->customers.id``."""
    child = ",".join(fk.columns)
    parent = ",".join(fk.ref_columns)
    return f"fk:{table}.{child}->{fk.ref_table}.{parent}"


class Relationship(FrozenModel):
    id: str
    child: str
    parent: str
    fk: ForeignKey
    cardinality: Cardinality
    is_self_reference: bool = False


class SchemaGraph:
    """networkx ``MultiDiGraph`` plus typed relationship metadata."""

    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        self.graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()
        self.relationships: dict[str, Relationship] = {}
        self.junctions: dict[str, JunctionTable] = {}

    def parents_of(self, table: str) -> list[Relationship]:
        return [r for r in self.relationships.values() if r.child == table]

    def children_of(self, table: str) -> list[Relationship]:
        return [r for r in self.relationships.values() if r.parent == table]

    @property
    def self_references(self) -> list[Relationship]:
        return [r for r in self.relationships.values() if r.is_self_reference]

    def relationship(self, rel_id: str) -> Relationship:
        return self.relationships[rel_id]


def build_graph(schema: Schema) -> SchemaGraph:
    """Build the graph. Foreign keys whose parent table is missing are skipped (see SEM001)."""
    sg = SchemaGraph(schema=schema)
    for name in schema.tables:
        sg.graph.add_node(name)
    for table in schema.tables.values():
        for fk in table.foreign_keys:
            if fk.ref_table not in schema.tables:
                continue
            rel = Relationship(
                id=fk_id(table.name, fk),
                child=table.name,
                parent=fk.ref_table,
                fk=fk,
                cardinality=infer_cardinality(table, fk),
                is_self_reference=is_self_reference(table, fk),
            )
            sg.relationships[rel.id] = rel
            sg.graph.add_edge(table.name, fk.ref_table, key=rel.id, relationship=rel)
        junction = detect_junction(table, schema)
        if junction is not None:
            sg.junctions[table.name] = junction
    return sg
