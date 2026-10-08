"""SchemaGraph (+ queries) -> IRProgram. A pure function."""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlglot import exp

from schemashift.ir.nodes import (
    AggregateCall,
    AggregateSemantics,
    AnyIRNode,
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
from schemashift.models.query import Query, TransactionBlock
from schemashift.models.schema import Schema, Table
from schemashift.semantic.graph import Relationship, SchemaGraph


class _Ids:
    """Hands out unique ids; repeated bases get a ``#n`` suffix."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def make(self, base: str) -> str:
        n = self._seen.get(base, 0)
        self._seen[base] = n + 1
        return base if n == 0 else f"{base}#{n + 1}"


def _cols(cols: Iterable[str]) -> str:
    return ",".join(cols)


def build_ir(graph: SchemaGraph, queries: Iterable[Query | TransactionBlock] = ()) -> IRProgram:
    """Lower a analysed schema (and optional queries) to the guarantee IR."""
    schema = graph.schema
    ids = _Ids()
    nodes: list[AnyIRNode] = []
    for table in schema.tables.values():
        _table_nodes(table, schema, ids, nodes)
    for rel in graph.relationships.values():
        _relationship_nodes(rel, schema, ids, nodes)
    for junction in graph.junctions.values():
        _junction_nodes(graph, junction.table, ids, nodes)
    for item in queries:
        _query_nodes(item, schema, graph, ids, nodes)
    return IRProgram(nodes=tuple(nodes))


# ---------------------------------------------------------------------- tables
def _table_nodes(table: Table, schema: Schema, ids: _Ids, out: list[AnyIRNode]) -> None:
    t = table.name
    out.append(
        EntityNode(
            id=ids.make(f"entity:{t}"), origin="CREATE TABLE", source_span=table.span, table=t
        )
    )
    for col in table.columns:
        c = col.name
        span = col.span
        out.append(
            AttributeNode(
                id=ids.make(f"attr:{t}.{c}"),
                origin="column definition",
                source_span=span,
                table=t,
                column=c,
                type=col.sql_type,
            )
        )
        out.append(
            TypeGuarantee(
                id=ids.make(f"type:{t}.{c}"),
                origin="column type",
                source_span=span,
                table=t,
                column=c,
                type=col.sql_type,
            )
        )
        if not col.nullable:
            out.append(
                NotNullGuarantee(
                    id=ids.make(f"not_null:{t}.{c}"),
                    origin="NOT NULL" if not _in_pk(table, c) else "PRIMARY KEY",
                    source_span=span,
                    table=t,
                    column=c,
                )
            )
        if col.default is not None:
            out.append(
                DefaultValue(
                    id=ids.make(f"default:{t}.{c}"),
                    origin="DEFAULT",
                    source_span=span,
                    table=t,
                    column=c,
                    expr_sql=col.default.sql,
                    default_kind=col.default.kind,
                )
            )
        if col.is_identity:
            out.append(
                AutoIncrement(
                    id=ids.make(f"auto_increment:{t}.{c}"),
                    origin="SERIAL/IDENTITY",
                    source_span=span,
                    table=t,
                    column=c,
                )
            )
        if col.sql_type.base == "ENUM" and col.sql_type.enum_name in schema.enums:
            enum = schema.enums[col.sql_type.enum_name]
            out.append(
                EnumDomain(
                    id=ids.make(f"enum:{t}.{c}"),
                    origin="ENUM type",
                    source_span=span,
                    table=t,
                    column=c,
                    enum_name=enum.name,
                    values=enum.values,
                )
            )
    if table.primary_key is not None:
        out.append(
            EntityUniqueness(
                id=ids.make(f"pk:{t}.{_cols(table.primary_key.columns)}"),
                origin="PRIMARY KEY",
                source_span=table.primary_key.span,
                table=t,
                columns=table.primary_key.columns,
            )
        )
    for uq in table.uniques:
        out.append(
            ValueUniqueness(
                id=ids.make(f"unique:{t}.{_cols(uq.columns)}"),
                origin="UNIQUE",
                source_span=uq.span,
                table=t,
                columns=uq.columns,
                nullable=_any_nullable(table, uq.columns),
            )
        )
    for ix in table.indexes:
        if not ix.unique:
            continue
        out.append(
            ValueUniqueness(
                id=ids.make(f"unique:{t}.{_cols(ix.columns)}"),
                origin="UNIQUE INDEX",
                source_span=ix.span,
                table=t,
                columns=ix.columns,
                nullable=_any_nullable(table, ix.columns),
                partial_where=ix.where_sql,
            )
        )
    for chk in table.checks:
        names = tuple(
            dict.fromkeys(str(c.name) for c in chk.parse().find_all(exp.Column) if c.name)
        )
        out.append(
            DomainConstraint(
                id=ids.make(f"check:{t}.{chk.name or _slug(chk.expression_sql)}"),
                origin="CHECK",
                source_span=chk.span,
                table=t,
                expr_sql=chk.expression_sql,
                name=chk.name,
                columns=names,
            )
        )


def _in_pk(table: Table, column: str) -> bool:
    return table.primary_key is not None and column in table.primary_key.columns


def _any_nullable(table: Table, cols: Iterable[str]) -> bool:
    for name in cols:
        col = table.column(name)
        if col is not None and col.nullable:
            return True
    return False


def _slug(sql: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", sql).strip("_")[:40]


# --------------------------------------------------------------- relationships
def _relationship_nodes(rel: Relationship, schema: Schema, ids: _Ids, out: list[AnyIRNode]) -> None:
    fk = rel.fk
    span = fk.span
    out.append(
        RelationshipNode(
            id=ids.make(f"rel:{rel.id.removeprefix('fk:')}"),
            origin="FOREIGN KEY",
            source_span=span,
            parent=rel.parent,
            child=rel.child,
            cardinality=rel.cardinality,
            fk_ref=rel.id,
            self_reference=rel.is_self_reference,
        )
    )
    out.append(
        ReferentialIntegrity(
            id=ids.make(rel.id),
            origin="FOREIGN KEY",
            source_span=span,
            child=rel.child,
            parent=rel.parent,
            columns=fk.columns,
            ref_columns=fk.ref_columns,
            fk_ref=rel.id,
        )
    )
    body = rel.id.removeprefix("fk:")
    if fk.on_delete == "CASCADE":
        out.append(
            CascadingDelete(
                id=ids.make(f"cascade_delete:{body}"),
                origin="ON DELETE CASCADE",
                source_span=span,
                parent=rel.parent,
                child=rel.child,
                fk_ref=rel.id,
            )
        )
    elif fk.on_delete == "SET NULL":
        out.append(
            SetNullOnDelete(
                id=ids.make(f"set_null:{body}"),
                origin="ON DELETE SET NULL",
                source_span=span,
                parent=rel.parent,
                child=rel.child,
                columns=fk.columns,
                fk_ref=rel.id,
            )
        )
    elif fk.on_delete == "SET DEFAULT":
        out.append(
            SetDefaultOnDelete(
                id=ids.make(f"set_default:{body}"),
                origin="ON DELETE SET DEFAULT",
                source_span=span,
                parent=rel.parent,
                child=rel.child,
                columns=fk.columns,
                fk_ref=rel.id,
            )
        )
    else:
        out.append(
            RestrictDelete(
                id=ids.make(f"restrict_delete:{body}"),
                origin=f"ON DELETE {fk.on_delete}",
                source_span=span,
                parent=rel.parent,
                child=rel.child,
                fk_ref=rel.id,
                action="RESTRICT" if fk.on_delete == "RESTRICT" else "NO ACTION",
            )
        )
    if fk.on_update == "CASCADE":
        out.append(
            CascadingUpdate(
                id=ids.make(f"cascade_update:{body}"),
                origin="ON UPDATE CASCADE",
                source_span=span,
                parent=rel.parent,
                child=rel.child,
                fk_ref=rel.id,
                parent_columns=fk.ref_columns,
            )
        )


def _junction_nodes(graph: SchemaGraph, table_name: str, ids: _Ids, out: list[AnyIRNode]) -> None:
    j = graph.junctions[table_name]
    table = graph.schema.tables[table_name]
    out.append(
        RelationshipNode(
            id=ids.make(f"rel:m2n:{j.left}-{j.right}-via-{j.table}"),
            origin="junction table",
            source_span=table.span,
            parent=j.left,
            child=j.right,
            cardinality="M:N",
            fk_ref=j.table,
            junction=j.table,
        )
    )
    key_cols = tuple(dict.fromkeys([*j.left_fk.columns, *j.right_fk.columns]))
    out.append(
        CrossEntityUniqueness(
            id=ids.make(f"cross_unique:{j.table}.{_cols(key_cols)}"),
            origin="junction table key",
            source_span=table.primary_key.span if table.primary_key else table.span,
            tables=(j.left, j.right),
            columns=key_cols,
            junction=j.table,
        )
    )


# --------------------------------------------------------------------- queries
def _table_scope(ast: exp.Expr, schema: Schema) -> dict[str, str]:
    """alias-or-name -> real table name for every known table in the statement."""
    scope: dict[str, str] = {}
    for tnode in ast.find_all(exp.Table):
        name = str(tnode.name)
        name = name if tnode.this is not None and tnode.this.args.get("quoted") else name.lower()
        if name not in schema.tables:
            continue
        scope[name] = name
        if tnode.alias:
            scope[tnode.alias.lower()] = name
    return scope


def _tables_in(ast: exp.Expr, schema: Schema) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_table_scope(ast, schema).values()))


def _resolve_column(col: exp.Column, scope: dict[str, str], schema: Schema) -> str:
    name = str(col.name)
    if col.table:
        table = scope.get(col.table.lower(), col.table)
        return f"{table}.{name}"
    owners = [t for t in dict.fromkeys(scope.values()) if schema.tables[t].column(name)]
    return f"{owners[0]}.{name}" if len(owners) == 1 else name


def _query_nodes(
    item: Query | TransactionBlock,
    schema: Schema,
    graph: SchemaGraph,
    ids: _Ids,
    out: list[AnyIRNode],
) -> None:
    if isinstance(item, TransactionBlock):
        touched: list[str] = []
        for stmt in item.statements:
            touched.extend(_tables_in(stmt.ast, schema))
        tables = tuple(dict.fromkeys(touched))
        if len(tables) >= 2:
            out.append(
                MultiEntityAtomicity(
                    id=ids.make(f"txn:{item.id}"),
                    origin="BEGIN ... COMMIT",
                    source_span=item.span,
                    tables=tables,
                    txn_id=item.id,
                )
            )
        for stmt in item.statements:
            _query_nodes(stmt, schema, graph, ids, out)
        return
    if item.kind == "SELECT":
        _join_nodes(item, schema, graph, ids, out)
        _aggregate_nodes(item, schema, ids, out)


def _join_nodes(
    q: Query, schema: Schema, graph: SchemaGraph, ids: _Ids, out: list[AnyIRNode]
) -> None:
    ast = q.ast
    if not isinstance(ast, exp.Select):
        return
    scope = _table_scope(ast, schema)
    from_ = ast.args.get("from_") or ast.args.get("from")
    base_table = _single_table(from_.this, scope) if from_ is not None else None
    projected_tables = _projected_tables(ast, scope, schema)
    for idx, join in enumerate(ast.args.get("joins") or []):
        right = _single_table(join.this, scope)
        on = join.args.get("on")
        if right is None or on is None:
            continue
        left = base_table
        for col in on.find_all(exp.Column):
            owner = scope.get(col.table.lower()) if col.table else None
            if owner is not None and owner != right:
                left = owner
                break
        if left is None:
            continue
        kind = "LEFT" if str(join.args.get("side") or "").upper() == "LEFT" else "INNER"
        out.append(
            JoinSemantics(
                id=ids.make(f"join:{q.id}#{idx}"),
                origin=f"{kind} JOIN",
                source_span=q.span,
                query_id=q.id,
                kind=kind,
                tables=(left, right),
                on_sql=on.sql(dialect="postgres"),
                fk_ref=_match_fk(graph, left, right, on, scope),
                right_columns_projected=right in projected_tables,
            )
        )


def _single_table(node: exp.Expr | None, scope: dict[str, str]) -> str | None:
    if isinstance(node, exp.Table):
        name = str(node.name)
        return scope.get(name.lower()) or scope.get(name)
    return None


def _projected_tables(ast: exp.Select, scope: dict[str, str], schema: Schema) -> set[str]:
    """Tables whose columns are projected *as plain values* (aggregated columns do not count)."""
    tables: set[str] = set()
    for proj in ast.expressions:
        if isinstance(proj, exp.Star):
            return set(scope.values())
        for col in proj.find_all(exp.Column):
            if col.find_ancestor(exp.AggFunc) is not None:
                continue
            if isinstance(col.this, exp.Star):
                if col.table:
                    tables.add(scope.get(col.table.lower(), col.table))
                else:
                    return set(scope.values())
            elif col.table:
                tables.add(scope.get(col.table.lower(), col.table))
            else:
                owners = [
                    t for t in dict.fromkeys(scope.values()) if schema.tables[t].column(col.name)
                ]
                tables.update(owners[:1])
    return tables


def _match_fk(
    graph: SchemaGraph, left: str, right: str, on: exp.Expr, scope: dict[str, str]
) -> str | None:
    pairs: set[tuple[str, str]] = set()
    for eq in on.find_all(exp.EQ):
        a, b = eq.this, eq.expression
        if isinstance(a, exp.Column) and isinstance(b, exp.Column) and a.table and b.table:
            ta, tb = scope.get(a.table.lower()), scope.get(b.table.lower())
            if ta and tb:
                pairs.add((f"{ta}.{a.name}", f"{tb}.{b.name}"))
                pairs.add((f"{tb}.{b.name}", f"{ta}.{a.name}"))
    for rel in graph.relationships.values():
        if {rel.child, rel.parent} != {left, right}:
            continue
        wanted = {
            (f"{rel.child}.{c}", f"{rel.parent}.{p}")
            for c, p in zip(rel.fk.columns, rel.fk.ref_columns, strict=False)
        }
        if wanted and wanted <= pairs:
            return rel.id
    return None


def _left_joined(ast: exp.Select, scope: dict[str, str]) -> tuple[str, ...]:
    tables: list[str] = []
    for join in ast.args.get("joins") or []:
        if str(join.args.get("side") or "").upper() == "LEFT":
            right = _single_table(join.this, scope)
            if right is not None:
                tables.append(right)
    return tuple(dict.fromkeys(tables))


def _aggregate_nodes(q: Query, schema: Schema, ids: _Ids, out: list[AnyIRNode]) -> None:
    ast = q.ast
    if not isinstance(ast, exp.Select):
        return
    funcs: dict[type[exp.Expr], str] = {
        exp.Count: "COUNT",
        exp.Sum: "SUM",
        exp.Avg: "AVG",
        exp.Min: "MIN",
        exp.Max: "MAX",
    }
    scope = _table_scope(ast, schema)
    calls: list[AggregateCall] = []
    for node in ast.find_all(*funcs):
        arg = node.this
        distinct = isinstance(arg, exp.Distinct)
        if distinct:
            arg = arg.expressions[0] if arg.expressions else None
        if isinstance(arg, exp.Star) or arg is None:
            text = "*"
        elif isinstance(arg, exp.Column):
            text = _resolve_column(arg, scope, schema)
        else:
            text = arg.sql(dialect="postgres")
        calls.append(AggregateCall(func=funcs[type(node)], argument=text, distinct=distinct))
    group = ast.args.get("group")
    group_by = tuple(
        _resolve_column(e, scope, schema)
        if isinstance(e, exp.Column)
        else e.sql(dialect="postgres")
        for e in (group.expressions if group is not None else [])
    )
    if not calls and not group_by:
        return
    out.append(
        AggregateSemantics(
            id=ids.make(f"aggregate:{q.id}"),
            origin="GROUP BY / aggregate",
            source_span=q.span,
            query_id=q.id,
            tables=tuple(dict.fromkeys(scope.values())),
            group_by=group_by,
            aggregates=tuple(calls),
            has_having=ast.args.get("having") is not None,
            left_joined_tables=_left_joined(ast, scope),
        )
    )
