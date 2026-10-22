"""Feature extraction per relationship (inputs to the cost model)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, TypeVar

from schemashift.ir.nodes import (
    CascadingDelete,
    IRProgram,
    MultiEntityAtomicity,
    SetDefaultOnDelete,
    SetNullOnDelete,
)
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import NormalizedType, Table, TypeBase
from schemashift.optimizer.stats import fanout_statistics
from schemashift.optimizer.weights import Weights, load_weights
from schemashift.optimizer.workload import (
    AccessHint,
    WriteFrequency,
    analyze_workload,
    seed_row_counts,
)
from schemashift.semantic.graph import Relationship, SchemaGraph

_T = TypeVar("_T")
Shape = Literal["1:1", "1:N", "self"]

# Rough BSON value sizes in bytes.
_TYPE_BYTES: dict[TypeBase, int] = {
    "SMALLINT": 4,
    "INTEGER": 4,
    "BIGINT": 8,
    "DECIMAL": 16,
    "FLOAT": 8,
    "DOUBLE": 8,
    "BOOLEAN": 1,
    "TEXT": 100,
    "VARCHAR": 50,
    "CHAR": 16,
    "DATE": 8,
    "TIMESTAMP": 8,
    "TIMESTAMPTZ": 8,
    "TIME": 16,
    "UUID": 16,
    "JSON": 200,
    "JSONB": 200,
    "BYTEA": 100,
    "ENUM": 10,
    "INTERVAL": 20,
}
_FIELD_OVERHEAD = 7  # type byte + key terminator + length prefixes
_DOC_OVERHEAD = 20  # document header + _id
_ARRAY_FACTOR = 5


def value_bytes(t: NormalizedType) -> int:
    size = _TYPE_BYTES[t.base]
    if t.base in ("VARCHAR", "CHAR") and t.length:
        size = min(t.length, 200)
    return size * _ARRAY_FACTOR if t.is_array else size


def estimate_doc_bytes(table: Table) -> int:
    """Estimated size of one row of ``table`` once it is a BSON document."""
    return _DOC_OVERHEAD + sum(
        len(c.name) + _FIELD_OVERHEAD + value_bytes(c.sql_type) for c in table.columns
    )


class RelationshipFeatures(FrozenModel):
    relationship_id: str
    parent: str
    child: str
    cardinality: Shape
    child_independent_access: float
    read_together_ratio: float
    child_write_frequency: WriteFrequency
    estimated_child_count_per_parent: int
    estimated_child_doc_size_bytes: int
    parent_doc_size_bytes: int
    child_has_other_parents: bool
    guarantees_needing_embedding: int
    is_junction_edge: bool = False
    child_fk_nullable: bool = False  # a child with a NULL foreign key has no parent document
    max_child_count_per_parent: int = 0  # worst parent in the seed data (0: not measured)
    fanout_skew: float = 0.0  # (max - avg) / avg fan-out measured from seed data
    child_write_share: float = 0.0  # frequency-weighted share of the workload writing the child
    parent_write_share: float = 0.0  # ... writing the parent
    workload_weighted: bool = False  # query frequencies or writes make the workload terms apply

    @property
    def worst_child_count(self) -> int:
        """Children per parent the hard constraints must hold for (the measured maximum, if any)."""
        return max(self.estimated_child_count_per_parent, self.max_child_count_per_parent)

    sources: dict[str, str] = {}  # feature -> "default" | "workload" | "seed" | "hint"


def _fk_nullable(child: Table, rel: Relationship) -> bool:
    cols = [child.column(c) for c in rel.fk.columns]
    return any(c is not None and c.nullable for c in cols)


def _other_parents(graph: SchemaGraph, rel: Relationship) -> bool:
    parents = {r.parent for r in graph.parents_of(rel.child) if not r.is_self_reference}
    return len(parents) >= 2


def _guarantee_count(rel: Relationship, program: IRProgram) -> int:
    count = 0
    for node in program.nodes:
        if isinstance(node, (CascadingDelete, SetNullOnDelete, SetDefaultOnDelete)):
            if node.fk_ref == rel.id:
                count += 1
        elif isinstance(node, MultiEntityAtomicity):
            if rel.parent in node.tables and rel.child in node.tables:
                count += 1
    return count


def _pick(
    src: dict[str, str], name: str, hinted: _T | None, measured: _T | None, default: _T
) -> _T:
    """hint > workload/seed measurement > default; records where the value came from."""
    if hinted is not None:
        src[name] = "hint"
        return hinted
    if measured is not None:
        src[name] = "workload"
        return measured
    src[name] = "default"
    return default


def extract_features(
    graph: SchemaGraph,
    program: IRProgram,
    queries: Iterable[Query | TransactionBlock] = (),
    seed_queries: Iterable[Query | TransactionBlock] = (),
    hints: Mapping[str, AccessHint] | None = None,
    weights: Weights | None = None,
    query_freq: Mapping[str, float] | None = None,
    default_freq: float = 1.0,
) -> dict[str, RelationshipFeatures]:
    """Features for every foreign-key relationship, keyed by relationship id.

    ``query_freq`` weights each statement (default 1); ``seed_queries`` give measured fan-out and
    sizes.
    """
    w = weights or load_weights()
    hints = hints or {}
    queries = list(queries)
    seed_queries = list(seed_queries)
    stats = analyze_workload(graph, queries, dict(query_freq or {}), default_freq)
    seed_counts = seed_row_counts(seed_queries)
    fan = fanout_statistics(graph, seed_queries)
    schema = graph.schema
    result: dict[str, RelationshipFeatures] = {}
    for rel in graph.relationships.values():
        hint = hints.get(rel.id) or AccessHint()
        wl = stats[rel.id]
        src: dict[str, str] = {}

        independent = _pick(
            src,
            "child_independent_access",
            hint.child_independent_access,
            wl.child_independent_access,
            w.defaults.child_independent_access,
        )
        together = _pick(
            src,
            "read_together_ratio",
            hint.read_together_ratio,
            wl.read_together_ratio,
            w.defaults.read_together_ratio,
        )
        freq = _pick(
            src,
            "child_write_frequency",
            hint.child_write_frequency,
            wl.child_write_frequency,
            w.defaults.child_write_frequency,
        )
        shape: Shape = "self" if rel.is_self_reference else rel.cardinality
        default_count = (
            w.defaults.children_per_parent_one_to_one
            if rel.cardinality == "1:1"
            else w.defaults.children_per_parent_one_to_many
        )
        seeded: int | None = None
        measured = fan.get(rel.id)
        if measured is not None:
            seeded = max(1, round(measured.avg))
        elif seed_counts.get(rel.child) and seed_counts.get(rel.parent):
            seeded = max(1, round(seed_counts[rel.child] / seed_counts[rel.parent]))
        count = _pick(
            src,
            "estimated_child_count_per_parent",
            hint.estimated_child_count_per_parent,
            seeded,
            default_count,
        )
        if src["estimated_child_count_per_parent"] == "workload":
            src["estimated_child_count_per_parent"] = "seed"
        child_table = schema.tables[rel.child]
        child_bytes = estimate_doc_bytes(child_table)
        if measured is not None and measured.child_row_value_bytes is not None:
            child_bytes = int(
                _DOC_OVERHEAD
                + sum(len(c.name) + _FIELD_OVERHEAD for c in child_table.columns)
                + measured.child_row_value_bytes
            )
            src["estimated_child_doc_size_bytes"] = "seed"
        if measured is not None:
            src["max_child_count_per_parent"] = "seed"
        if wl.weighted:
            src["workload_terms"] = "workload"
        result[rel.id] = RelationshipFeatures(
            relationship_id=rel.id,
            parent=rel.parent,
            child=rel.child,
            cardinality=shape,
            child_independent_access=float(independent),
            read_together_ratio=float(together),
            child_write_frequency=freq,
            estimated_child_count_per_parent=int(count),
            estimated_child_doc_size_bytes=child_bytes,
            max_child_count_per_parent=measured.max if measured is not None else 0,
            fanout_skew=measured.skew if measured is not None else 0.0,
            child_write_share=wl.child_write_share or 0.0,
            parent_write_share=wl.parent_write_share or 0.0,
            workload_weighted=wl.weighted,
            parent_doc_size_bytes=estimate_doc_bytes(schema.tables[rel.parent]),
            child_has_other_parents=_other_parents(graph, rel),
            guarantees_needing_embedding=_guarantee_count(rel, program),
            is_junction_edge=rel.child in graph.junctions,
            child_fk_nullable=_fk_nullable(schema.tables[rel.child], rel),
            sources=src,
        )
    return result
