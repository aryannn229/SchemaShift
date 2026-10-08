"""IR node classes. Nodes encode *guarantees* (what must stay true), not SQL syntax.

Every node has a stable ``id``, a ``source_span`` and an ``origin`` naming the SQL construct
that produced it. Nodes are frozen pydantic models; the discriminator is ``node_type``.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import Field

from schemashift.models.base import FrozenModel
from schemashift.models.schema import NormalizedType
from schemashift.models.source import SourceSpan

IRCardinality = Literal["1:1", "1:N", "M:N"]


class IRNode(FrozenModel):
    id: str
    origin: str
    source_span: SourceSpan | None = None


# ---------------------------------------------------------------- entity nodes
class EntityNode(IRNode):
    node_type: Literal["entity"] = "entity"
    table: str


class AttributeNode(IRNode):
    node_type: Literal["attribute"] = "attribute"
    table: str
    column: str
    type: NormalizedType


class RelationshipNode(IRNode):
    node_type: Literal["relationship"] = "relationship"
    parent: str
    child: str
    cardinality: IRCardinality
    fk_ref: str  # FK id, or the junction table name for M:N
    self_reference: bool = False
    junction: str | None = None


# ------------------------------------------------------------- guarantee nodes
class ReferentialIntegrity(IRNode):
    node_type: Literal["referential_integrity"] = "referential_integrity"
    child: str
    parent: str
    columns: tuple[str, ...]
    ref_columns: tuple[str, ...]
    fk_ref: str


class CascadingDelete(IRNode):
    node_type: Literal["cascading_delete"] = "cascading_delete"
    parent: str
    child: str
    fk_ref: str


class CascadingUpdate(IRNode):
    node_type: Literal["cascading_update"] = "cascading_update"
    parent: str
    child: str
    fk_ref: str
    parent_columns: tuple[str, ...] = ()


class SetNullOnDelete(IRNode):
    node_type: Literal["set_null_on_delete"] = "set_null_on_delete"
    parent: str
    child: str
    columns: tuple[str, ...]
    fk_ref: str


class SetDefaultOnDelete(IRNode):
    node_type: Literal["set_default_on_delete"] = "set_default_on_delete"
    parent: str
    child: str
    columns: tuple[str, ...]
    fk_ref: str


class RestrictDelete(IRNode):
    node_type: Literal["restrict_delete"] = "restrict_delete"
    parent: str
    child: str
    fk_ref: str
    action: Literal["RESTRICT", "NO ACTION"] = "NO ACTION"


class EntityUniqueness(IRNode):
    node_type: Literal["entity_uniqueness"] = "entity_uniqueness"
    table: str
    columns: tuple[str, ...]


class ValueUniqueness(IRNode):
    node_type: Literal["value_uniqueness"] = "value_uniqueness"
    table: str
    columns: tuple[str, ...]
    nullable: bool = False  # any key column nullable
    partial_where: str | None = None  # partial unique index predicate


class CrossEntityUniqueness(IRNode):
    """Uniqueness of a *combination of related entities* (the key of a junction table)."""

    node_type: Literal["cross_entity_uniqueness"] = "cross_entity_uniqueness"
    tables: tuple[str, ...]  # the related entities (left, right)
    columns: tuple[str, ...]
    junction: str


class NotNullGuarantee(IRNode):
    node_type: Literal["not_null"] = "not_null"
    table: str
    column: str


class DomainConstraint(IRNode):
    node_type: Literal["domain_constraint"] = "domain_constraint"
    table: str
    expr_sql: str
    name: str | None = None
    columns: tuple[str, ...] = ()


class TypeGuarantee(IRNode):
    node_type: Literal["type_guarantee"] = "type_guarantee"
    table: str
    column: str
    type: NormalizedType


class DefaultValue(IRNode):
    node_type: Literal["default_value"] = "default_value"
    table: str
    column: str
    expr_sql: str
    default_kind: Literal["literal", "now", "expression"]


class AutoIncrement(IRNode):
    node_type: Literal["auto_increment"] = "auto_increment"
    table: str
    column: str


class EnumDomain(IRNode):
    node_type: Literal["enum_domain"] = "enum_domain"
    table: str
    column: str
    enum_name: str
    values: tuple[str, ...]


class MultiEntityAtomicity(IRNode):
    node_type: Literal["multi_entity_atomicity"] = "multi_entity_atomicity"
    tables: tuple[str, ...]
    txn_id: str


class JoinSemantics(IRNode):
    node_type: Literal["join_semantics"] = "join_semantics"
    query_id: str
    kind: Literal["INNER", "LEFT"]
    tables: tuple[str, str]  # (left side, joined table)
    on_sql: str
    fk_ref: str | None = None
    right_columns_projected: bool = False


class AggregateCall(FrozenModel):
    func: Literal["COUNT", "SUM", "AVG", "MIN", "MAX"]
    argument: str  # "*" or a column reference
    distinct: bool = False


class AggregateSemantics(IRNode):
    node_type: Literal["aggregate_semantics"] = "aggregate_semantics"
    query_id: str
    tables: tuple[str, ...]
    group_by: tuple[str, ...] = ()
    aggregates: tuple[AggregateCall, ...] = ()
    has_having: bool = False


AnyIRNode = Annotated[
    EntityNode
    | AttributeNode
    | RelationshipNode
    | ReferentialIntegrity
    | CascadingDelete
    | CascadingUpdate
    | SetNullOnDelete
    | SetDefaultOnDelete
    | RestrictDelete
    | EntityUniqueness
    | ValueUniqueness
    | CrossEntityUniqueness
    | NotNullGuarantee
    | DomainConstraint
    | TypeGuarantee
    | DefaultValue
    | AutoIncrement
    | EnumDomain
    | MultiEntityAtomicity
    | JoinSemantics
    | AggregateSemantics,
    Field(discriminator="node_type"),
]

_T = TypeVar("_T", bound=IRNode)

ENTITY_NODE_TYPES = frozenset({"entity", "attribute", "relationship"})


class IRProgram(FrozenModel):
    """A flat list of entity and guarantee nodes."""

    nodes: tuple[AnyIRNode, ...]

    @property
    def entities(self) -> tuple[IRNode, ...]:
        return tuple(n for n in self.nodes if n.node_type in ENTITY_NODE_TYPES)

    @property
    def guarantees(self) -> tuple[IRNode, ...]:
        return tuple(n for n in self.nodes if n.node_type not in ENTITY_NODE_TYPES)

    def by_id(self, node_id: str) -> IRNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def of_type(self, cls: type[_T]) -> tuple[_T, ...]:
        return tuple(n for n in self.nodes if isinstance(n, cls))
