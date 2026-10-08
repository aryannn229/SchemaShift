"""Human readable IR dump (debugging + the UI's IR tab)."""

from __future__ import annotations

from schemashift.ir.nodes import (
    AggregateSemantics,
    AttributeNode,
    AutoIncrement,
    CascadingDelete,
    CascadingUpdate,
    CrossEntityUniqueness,
    DefaultValue,
    DomainConstraint,
    EntityNode,
    EntityUniqueness,
    EnumDomain,
    IRProgram,
    JoinSemantics,
    MultiEntityAtomicity,
    NotNullGuarantee,
    ReferentialIntegrity,
    RelationshipNode,
    RestrictDelete,
    SetDefaultOnDelete,
    SetNullOnDelete,
    TypeGuarantee,
    ValueUniqueness,
)
from schemashift.models.schema import NormalizedType


def _type(t: NormalizedType) -> str:
    base: str = t.base
    if t.length:
        base += f"({t.length})"
    if t.precision is not None:
        base += f"({t.precision},{t.scale or 0})"
    if t.enum_name:
        base += f":{t.enum_name}"
    return base + ("[]" if t.is_array else "")


def describe(node: object) -> str:
    """One-line meaning of a node."""
    if isinstance(node, EntityNode):
        return f"Entity {node.table}"
    if isinstance(node, AttributeNode):
        return f"Attribute {node.table}.{node.column}: {_type(node.type)}"
    if isinstance(node, RelationshipNode):
        via = f" via {node.junction}" if node.junction else ""
        return f"Relationship {node.parent} {node.cardinality} {node.child}{via}"
    if isinstance(node, ReferentialIntegrity):
        return (
            f"{node.child}({', '.join(node.columns)}) must reference an existing "
            f"{node.parent}({', '.join(node.ref_columns)})"
        )
    if isinstance(node, CascadingDelete):
        return f"deleting a {node.parent} deletes its {node.child} rows"
    if isinstance(node, CascadingUpdate):
        return f"updating a {node.parent} key updates {node.child} references"
    if isinstance(node, SetNullOnDelete):
        return f"deleting a {node.parent} sets {node.child}({', '.join(node.columns)}) to NULL"
    if isinstance(node, SetDefaultOnDelete):
        return f"deleting a {node.parent} resets {node.child}({', '.join(node.columns)}) to default"
    if isinstance(node, RestrictDelete):
        return f"a {node.parent} with {node.child} rows cannot be deleted ({node.action})"
    if isinstance(node, EntityUniqueness):
        return f"{node.table}({', '.join(node.columns)}) uniquely identifies a row"
    if isinstance(node, ValueUniqueness):
        extra = " [nullable]" if node.nullable else ""
        extra += f" WHERE {node.partial_where}" if node.partial_where else ""
        return f"{node.table}({', '.join(node.columns)}) has no duplicate values{extra}"
    if isinstance(node, CrossEntityUniqueness):
        return (
            f"each ({', '.join(node.columns)}) combination of {' and '.join(node.tables)} "
            f"appears once (junction {node.junction})"
        )
    if isinstance(node, NotNullGuarantee):
        return f"{node.table}.{node.column} is always present"
    if isinstance(node, DomainConstraint):
        return f"{node.table}: {node.expr_sql}"
    if isinstance(node, TypeGuarantee):
        return f"{node.table}.{node.column} holds exactly {_type(node.type)}"
    if isinstance(node, DefaultValue):
        return f"{node.table}.{node.column} defaults to {node.expr_sql} ({node.default_kind})"
    if isinstance(node, AutoIncrement):
        return f"{node.table}.{node.column} is server-generated and increasing"
    if isinstance(node, EnumDomain):
        return f"{node.table}.{node.column} in {{{', '.join(node.values)}}}"
    if isinstance(node, MultiEntityAtomicity):
        return f"{node.txn_id}: all-or-nothing across {', '.join(node.tables)}"
    if isinstance(node, JoinSemantics):
        left, right = node.tables
        return f"{node.query_id}: {node.kind} JOIN {left} -> {right} ON {node.on_sql}"
    if isinstance(node, AggregateSemantics):
        aggs = ", ".join(f"{a.func}({a.argument})" for a in node.aggregates)
        grp = f" GROUP BY {', '.join(node.group_by)}" if node.group_by else ""
        return f"{node.query_id}: {aggs or 'grouping'}{grp}"
    return type(node).__name__


def print_ir(program: IRProgram) -> str:
    """Render the program as text, entities first then guarantees grouped by node type."""
    lines: list[str] = ["== ENTITIES =="]
    for n in program.entities:
        lines.append(f"  [{n.id}] {describe(n)}")
    lines.append("== GUARANTEES ==")
    current = ""
    for n in program.guarantees:
        kind = type(n).__name__
        if kind != current:
            lines.append(f"-- {kind}")
            current = kind
        lines.append(f"  [{n.id}] {describe(n)}  <{n.origin}>")
    return "\n".join(lines) + "\n"
