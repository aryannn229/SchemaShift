"""Document layout: where every table's data lives inside MongoDB documents.

A table is one of:
- ``root``:   its own collection;
- ``object``: a 1:1 child embedded as a sub-document of its host;
- ``array``:  a 1:N child embedded as an array of sub-documents;
- ``ref_scalars``: a pure junction folded into an array of scalar references;
- ``ref_docs``: a junction folded into an array of sub-documents (reference + payload).

The layout is the single source of truth shared by validators, indexes, query translation and
the data migration.
"""

from __future__ import annotations

from typing import Literal

from schemashift.models.base import FrozenModel
from schemashift.models.placement import PlacementPlan, junction_key
from schemashift.models.schema import Table
from schemashift.semantic.graph import Relationship, SchemaGraph

Kind = Literal["root", "object", "array", "ref_scalars", "ref_docs"]


class TableLayout(FrozenModel):
    table: str
    kind: Kind
    collection: str  # collection that stores the data
    host: str | None = None  # table whose document hosts this one
    relationship_id: str | None = None  # fk id (embedded) or m2n key (folded junction)
    path: tuple[str, ...] = ()  # field path from the collection root (empty for roots)
    dropped_columns: tuple[str, ...] = ()  # columns implied by nesting (FK to the host)
    host_columns: tuple[str, ...] = ()  # host columns the dropped FK referenced
    id_column: str | None = None  # column stored as _id (root with single-column PK)
    fields: dict[str, str] = {}  # column -> field name inside this table's document
    scalar_column: str | None = None  # ref_scalars: the column that *is* the array element

    @property
    def is_root(self) -> bool:
        return self.kind == "root"

    @property
    def is_array(self) -> bool:
        return self.kind in ("array", "ref_scalars", "ref_docs")

    def field_of(self, column: str) -> str:
        return self.fields[column]

    def has_column(self, column: str) -> bool:
        return column in self.fields or column == self.scalar_column

    def dotted(self, column: str) -> str:
        """Dotted path of ``column`` from the collection root (``$`` not included)."""
        if column == self.scalar_column:
            return ".".join(self.path)
        return ".".join((*self.path, self.fields[column]))


def pluralize_id(column: str) -> str:
    """``product_id`` -> ``product_ids``."""
    return f"{column}s"


def build_layouts(graph: SchemaGraph, plan: PlacementPlan) -> dict[str, TableLayout]:
    """Compute the layout of every table for a placement plan."""
    schema = graph.schema
    embed_rel: dict[str, Relationship] = {}
    for rel in graph.relationships.values():
        if rel.is_self_reference or not plan.is_embedded(rel.id):
            continue
        embed_rel.setdefault(rel.child, rel)  # planner guarantees at most one

    folded: dict[str, str] = {}  # junction table -> host
    for j in graph.junctions.values():
        decision = plan.decisions.get(junction_key(j.table))
        if decision is not None and decision.decision == "REF_ARRAY" and decision.host:
            folded[j.table] = decision.host

    layouts: dict[str, TableLayout] = {}

    def host_of(table: str) -> str | None:
        if table in folded:
            return folded[table]
        rel = embed_rel.get(table)
        return rel.parent if rel else None

    def build(table: str, stack: frozenset[str]) -> TableLayout:
        if table in layouts:
            return layouts[table]
        t = schema.tables[table]
        host = host_of(table)
        if host is None or table in stack or host not in schema.tables:
            layout = _root_layout(t)
        elif table in folded:
            layout = _folded_layout(graph, t, build(host, stack | {table}), plan)
        else:
            layout = _embedded_layout(embed_rel[table], t, build(host, stack | {table}), graph)
        layouts[table] = layout
        return layout

    for name in schema.tables:
        build(name, frozenset())
    return layouts


def _root_layout(t: Table) -> TableLayout:
    id_column = (
        t.primary_key.columns[0] if t.primary_key and len(t.primary_key.columns) == 1 else None
    )
    fields = {c.name: ("_id" if c.name == id_column else c.name) for c in t.columns}
    return TableLayout(
        table=t.name, kind="root", collection=t.name, id_column=id_column, fields=fields
    )


def _unique_field(host_fields: set[str], name: str) -> str:
    return name if name not in host_fields else f"{name}_docs"


def _embedded_layout(
    rel: Relationship, t: Table, host: TableLayout, graph: SchemaGraph
) -> TableLayout:
    dropped = rel.fk.columns
    field_name = _unique_field(set(host.fields.values()), t.name)
    fields = {c.name: c.name for c in t.columns if c.name not in dropped}
    return TableLayout(
        table=t.name,
        kind="object" if rel.cardinality == "1:1" else "array",
        collection=host.collection,
        host=host.table,
        relationship_id=rel.id,
        path=(*host.path, field_name),
        dropped_columns=dropped,
        host_columns=rel.fk.ref_columns,
        fields=fields,
    )


def _folded_layout(
    graph: SchemaGraph, t: Table, host: TableLayout, plan: PlacementPlan
) -> TableLayout:
    j = graph.junctions[t.name]
    host_fk = j.left_fk if j.left_fk.ref_table == host.table else j.right_fk
    other_fk = j.right_fk if host_fk is j.left_fk else j.left_fk
    pure = not j.payload_columns and len(other_fk.columns) == 1
    dropped = host_fk.columns
    key = junction_key(t.name)
    if pure:
        column = other_fk.columns[0]
        field_name = _unique_field(set(host.fields.values()), pluralize_id(column))
        return TableLayout(
            table=t.name,
            kind="ref_scalars",
            collection=host.collection,
            host=host.table,
            relationship_id=key,
            path=(*host.path, field_name),
            dropped_columns=dropped,
            host_columns=host_fk.ref_columns,
            fields={},
            scalar_column=column,
        )
    field_name = _unique_field(set(host.fields.values()), t.name)
    return TableLayout(
        table=t.name,
        kind="ref_docs",
        collection=host.collection,
        host=host.table,
        relationship_id=key,
        path=(*host.path, field_name),
        dropped_columns=dropped,
        host_columns=host_fk.ref_columns,
        fields={c.name: c.name for c in t.columns if c.name not in dropped},
    )


def children_of(layouts: dict[str, TableLayout], table: str) -> list[TableLayout]:
    """Layouts embedded directly inside ``table``'s document."""
    return sorted((lay for lay in layouts.values() if lay.host == table), key=lambda lay: lay.table)


def roots(layouts: dict[str, TableLayout]) -> list[TableLayout]:
    return sorted((lay for lay in layouts.values() if lay.is_root), key=lambda lay: lay.table)
