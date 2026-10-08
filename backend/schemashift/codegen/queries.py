"""SQL -> MongoDB translation for SELECT (aggregation pipelines) and INSERT/UPDATE/DELETE."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlglot import exp

from schemashift.codegen.layout import TableLayout
from schemashift.codegen.predicate import (
    NO_HOOK,
    ColRef,
    Untranslatable,
    conjuncts,
    literal_value,
    parse_predicate,
    to_expr,
    to_query,
    value_expr,
)
from schemashift.codegen.values import DecimalValue, NowValue, RawJs
from schemashift.models.base import FrozenModel
from schemashift.models.query import Query, QueryKind, TransactionBlock
from schemashift.models.schema import Column, NormalizedType, Schema, Table
from schemashift.semantic.graph import SchemaGraph


class TranslationError(Exception):
    """A query that cannot be translated for the chosen layout (message is user facing)."""


@dataclass(frozen=True)
class QueryContext:
    schema: Schema
    graph: SchemaGraph
    layouts: dict[str, TableLayout]
    uuid_as: str = "binary"
    preserve_integer_ids: bool = False


class WriteOp(FrozenModel):
    op: str  # insertOne | insertMany | updateOne | updateMany | deleteMany
    collection: str
    filter: dict[str, Any] | None = None
    update: Any = None
    documents: list[Any] | None = None
    array_filters: list[dict[str, Any]] | None = None


class TranslatedQuery(FrozenModel):
    query_id: str
    sql: str
    kind: QueryKind
    collection: str | None = None
    pipeline: list[Any] | None = None
    operations: list[WriteOp] = []
    output_columns: list[str] = []
    ordered: bool = False
    order_positions: list[int] | None = None  # output positions of the ORDER BY keys
    global_aggregate: bool = False  # aggregate without GROUP BY
    notes: list[str] = []
    error: str | None = None
    transaction_id: str | None = None


# ============================================================================= helpers
def _ident(node: exp.Identifier | None) -> str:
    assert node is not None
    name = str(node.name)
    return name if node.args.get("quoted") else name.lower()


def _table_name(t: exp.Table) -> str:
    return _ident(t.this) if isinstance(t.this, exp.Identifier) else str(t.name).lower()


def _alias_of(t: exp.Table) -> str:
    alias = t.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Identifier):
        return _ident(alias.this)
    return _table_name(t)


def _col_name(c: exp.Column) -> str:
    return _ident(c.this) if isinstance(c.this, exp.Identifier) else str(c.name).lower()


def _col_qualifier(c: exp.Column) -> str:
    tbl = c.args.get("table")
    return _ident(tbl) if isinstance(tbl, exp.Identifier) else ""


def one_line(sql: str) -> str:
    return " ".join(sql.split())


_AGG = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
_AGG_NAME = {exp.Count: "count", exp.Sum: "sum", exp.Avg: "avg", exp.Min: "min", exp.Max: "max"}


def has_aggregate(node: exp.Expr) -> bool:
    return any(True for _ in node.find_all(*_AGG))


def typed_value(node: exp.Expr, column: Column | None) -> Any:
    """Literal -> Python/BSON value converted for the target column (writes)."""
    hint = column.sql_type if column else None
    value = literal_value(node, hint)
    if value is None or hint is None:
        return value
    if hint.base == "DECIMAL" and isinstance(value, (int, float)) and not hint.is_array:
        return DecimalValue(str(Decimal(str(value))))
    return value


# =================================================================== SELECT translation
@dataclass
class _Binding:
    alias: str
    table: str
    layout: TableLayout
    ns: str


@dataclass
class _Step:
    stage: Any  # a pipeline stage dict, or None for placeholders
    provides: set[str] = field(default_factory=set)  # aliases made available by this step


class _Select:
    def __init__(self, q: Query, ctx: QueryContext) -> None:
        if not isinstance(q.ast, exp.Select):
            raise TranslationError("only SELECT statements are supported here")
        self.q, self.ctx, self.ast = q, ctx, q.ast
        self.bindings: dict[str, _Binding] = {}
        self.order: list[str] = []
        self.steps: list[_Step] = []
        self.root_ns: dict[str, str] = {}  # collection -> namespace holding its root document
        self.pseudo_ns: str | None = None
        self.source_ns = ""
        self.source_collection = ""
        self.notes: list[str] = []
        # aggregation state
        self.group_exprs: list[tuple[Any, Any]] = []  # (identity key, mongo expr)
        self.aggs: dict[str, tuple[str, dict[str, Any], Any]] = {}  # sql -> (name, accs, proj)
        self.in_group = False
        self.select_items: list[tuple[str, exp.Expr]] = []
        self.order_positions: list[int] | None = None
        self.global_aggregate = False

    # ------------------------------------------------------------------ scope
    def _layout(self, table: str) -> TableLayout:
        if table not in self.ctx.layouts:
            raise TranslationError(f"unknown table '{table}'")
        return self.ctx.layouts[table]

    def _bind(self, alias: str, table: str, ns: str) -> None:
        if alias in self.bindings:
            raise TranslationError(f"duplicate table alias '{alias}'")
        self.bindings[alias] = _Binding(alias, table, self._layout(table), ns)
        self.order.append(alias)

    def _unwinds(self, b: _Binding, preserve: bool) -> list[_Step]:
        """Unwind every array on the path of an embedded alias (once per namespace/path)."""
        steps: list[_Step] = []
        layout = b.layout
        chain = self._chain(layout)
        for lay in chain:
            key = (b.ns, lay.path)
            if key in self._unwound:
                continue
            self._unwound.add(key)
            path = f"${b.ns}.{'.'.join(lay.path)}"
            if lay.is_array:
                steps.append(
                    _Step(
                        {"$unwind": {"path": path, "preserveNullAndEmptyArrays": preserve}},
                        {b.alias},
                    )
                )
            elif not preserve:
                steps.append(
                    _Step(
                        {"$match": {f"{b.ns}.{'.'.join(lay.path)}": {"$exists": True}}}, {b.alias}
                    )
                )
        return steps

    def _chain(self, layout: TableLayout) -> list[TableLayout]:
        chain: list[TableLayout] = []
        cur: TableLayout | None = layout
        while cur is not None and not cur.is_root:
            chain.append(cur)
            cur = self.ctx.layouts.get(cur.host or "")
        return list(reversed(chain))

    def build_scope(self) -> None:
        self._unwound: set[tuple[str, tuple[str, ...]]] = set()
        from_ = self.ast.args.get("from_") or self.ast.args.get("from")
        if from_ is None or not isinstance(from_.this, exp.Table):
            raise TranslationError("SELECT without a FROM table is not supported")
        base: exp.Table = from_.this
        table = _table_name(base)
        layout = self._layout(table)
        alias = _alias_of(base)
        if layout.is_root:
            ns = alias
        else:
            ns = f"_{layout.collection}"
            self.pseudo_ns = ns
        self.source_ns, self.source_collection = ns, layout.collection
        self.root_ns[layout.collection] = ns
        self._bind(alias, table, ns)
        if not layout.is_root:
            self.steps.extend(self._unwinds(self.bindings[alias], preserve=False))
        for join in self.ast.args.get("joins") or []:
            self._join(join)

    def _equalities(self, on: exp.Expr) -> list[tuple[exp.Expr, exp.Expr]]:
        out: list[tuple[exp.Expr, exp.Expr]] = []
        for term in conjuncts(on):
            if not isinstance(term, exp.EQ):
                raise TranslationError("join conditions must be equalities")
            out.append((term.this, term.expression))
        return out

    def _aliases_in(self, node: exp.Expr, known: dict[str, _Binding] | None = None) -> set[str]:
        found: set[str] = set()
        for c in node.find_all(exp.Column):
            q = _col_qualifier(c)
            if q:
                found.add(q)
            else:
                owners = [
                    b.alias
                    for b in (known or self.bindings).values()
                    if b.table in self.ctx.schema.tables
                    and self.ctx.schema.tables[b.table].column(_col_name(c))
                ]
                found.update(owners[:1])
        return found

    def _join(self, join: exp.Join) -> None:
        target = join.this
        if not isinstance(target, exp.Table):
            raise TranslationError("only joins against tables are supported")
        table, alias = _table_name(target), _alias_of(target)
        layout = self._layout(table)
        left = str(join.args.get("side") or "").upper() == "LEFT"
        eqs = self._equalities(join.args["on"])
        if self._is_host_of_bound(table):
            # the joined table is the (already unwound) host of an embedded alias in scope
            child_alias = self._embedded_alias_for(table)
            ns = self.bindings[child_alias].ns
            leftover = self._drop_fk_equalities(alias, eqs, embedded_alias=child_alias)
            self._bind(alias, table, ns)
            if self.pseudo_ns == ns and layout.is_root:
                self.pseudo_ns = None
            self._extra_on(leftover, left, alias)
            self.steps.append(_Step(None, {alias}))
            return
        if not layout.is_root:
            self._join_embedded(alias, table, layout, eqs, left)
            return
        self._bind(alias, table, alias)
        self.root_ns.setdefault(layout.collection, alias)
        self.steps.append(self._lookup(alias, layout, eqs, left))

    def _is_host_of_bound(self, table: str) -> bool:
        """True when an embedded alias in scope is nested directly in ``table`` and no alias of
        ``table`` itself is bound in that namespace yet."""
        for b in self.bindings.values():
            if b.layout.host == table and not any(
                o.table == table and o.ns == b.ns for o in self.bindings.values()
            ):
                return True
        return False

    def _embedded_alias_for(self, host_table: str) -> str:
        return next(
            b.alias
            for b in self.bindings.values()
            if b.layout.host == host_table
            and not any(o.table == host_table and o.ns == b.ns for o in self.bindings.values())
        )

    def _drop_fk_equalities(
        self, alias: str, eqs: list[tuple[exp.Expr, exp.Expr]], embedded_alias: str
    ) -> list[tuple[exp.Expr, exp.Expr]]:
        """Remove the equality that restates the embedding foreign key (implicit in nesting)."""
        b = self.bindings[embedded_alias]
        dropped = set(b.layout.dropped_columns)
        leftover: list[tuple[exp.Expr, exp.Expr]] = []
        matched = False
        for lhs, rhs in eqs:
            sides = [x for x in (lhs, rhs) if isinstance(x, exp.Column)]
            if len(sides) == 2 and any(
                _col_name(c) in dropped and (_col_qualifier(c) or embedded_alias) == embedded_alias
                for c in sides
            ):
                matched = True
                continue
            leftover.append((lhs, rhs))
        if not matched:
            raise TranslationError(
                f"{b.table} is embedded in {b.layout.host}: join it using its foreign key "
                f"({', '.join(sorted(dropped))})"
            )
        return leftover

    def _extra_on(
        self, leftover: list[tuple[exp.Expr, exp.Expr]], left_join: bool, alias: str
    ) -> None:
        if not leftover:
            return
        if left_join:
            raise TranslationError(
                "extra ON conditions on a LEFT join over an embedded relationship"
            )
        cond = {"$and": [{"$eq": [self._expr(lhs), self._expr(rhs)]} for lhs, rhs in leftover]}
        self.steps.append(_Step({"$match": {"$expr": cond}}, {alias}))

    def _join_embedded(
        self,
        alias: str,
        table: str,
        layout: TableLayout,
        eqs: list[tuple[exp.Expr, exp.Expr]],
        left: bool,
    ) -> None:
        coll = layout.collection
        if coll in self.root_ns:
            ns = self.root_ns[coll]
            self._bind(alias, table, ns)
            leftover = self._drop_fk_equalities(alias, eqs, embedded_alias=alias)
            self.steps.extend(self._unwinds(self.bindings[alias], preserve=left))
            self._extra_on(leftover, left, alias)
            if not any(alias in s.provides for s in self.steps):
                self.steps.append(_Step(None, {alias}))
            return
        # the host collection is not in the pipeline yet: look it up, then keep matching elements
        host_ns = f"{alias}__host"
        host_layout = self.ctx.layouts[layout.host or ""]
        dropped = list(layout.dropped_columns)
        key_eqs: list[tuple[str, exp.Expr]] = []
        own_eqs: list[tuple[str, exp.Expr]] = []
        for lhs, rhs in eqs:
            for mine, other in ((lhs, rhs), (rhs, lhs)):
                if isinstance(mine, exp.Column) and self._belongs(mine, alias):
                    (key_eqs if _col_name(mine) in dropped else own_eqs).append(
                        (_col_name(mine), other)
                    )
                    break
            else:
                raise TranslationError(f"each join equality must reference {table}")
        if key_eqs:
            col, other = key_eqs.pop(0)
            foreign = host_layout.dotted(layout.host_columns[dropped.index(col)])
        elif own_eqs:
            col, other = own_eqs[0]
            if not layout.has_column(col):
                raise TranslationError(f"unknown column '{col}' in join")
            foreign = layout.dotted(col)
        else:
            raise TranslationError(
                f"{table} is embedded in {layout.host}: join it using its foreign key"
            )
        local = self._expr(other)
        if not isinstance(local, str) or not local.startswith("$"):
            raise TranslationError("join conditions on embedded tables must compare plain columns")
        self.steps.append(
            _Step(
                {
                    "$lookup": {
                        "from": coll,
                        "localField": local[1:],
                        "foreignField": foreign,
                        "as": host_ns,
                    }
                },
                {alias},
            )
        )
        self.steps.append(
            _Step({"$unwind": {"path": f"${host_ns}", "preserveNullAndEmptyArrays": left}}, {alias})
        )
        if key_eqs:
            raise TranslationError(
                f"{table}: composite foreign keys in this join are not supported"
            )
        element_conditions: list[Any] = []
        for col2, other2 in own_eqs:
            if not layout.has_column(col2):
                raise TranslationError(f"unknown column '{col2}' in join")
            local2 = self._expr(other2)
            element_conditions.append(
                {
                    "$and": [
                        {"$gt": [local2, None]},
                        {"$eq": [f"$$el.{layout.fields[col2]}", local2]},
                    ]
                }
            )
        if element_conditions:
            if not layout.is_array or sum(1 for c in self._chain(layout) if c.is_array) != 1:
                raise TranslationError(
                    f"{table}: extra join conditions need a single embedded array level"
                )
            array_path = f"{host_ns}.{'.'.join(layout.path)}"
            cond = (
                element_conditions[0]
                if len(element_conditions) == 1
                else {"$and": element_conditions}
            )
            self.steps.append(
                _Step(
                    {
                        "$addFields": {
                            array_path: {
                                "$filter": {"input": f"${array_path}", "as": "el", "cond": cond}
                            }
                        }
                    },
                    {alias},
                )
            )
        self.root_ns[coll] = host_ns
        self._bind(alias, table, host_ns)
        self._unwound.add((host_ns, ()))
        self.steps.extend(self._unwinds(self.bindings[alias], preserve=left))

    def _lookup(
        self, alias: str, layout: TableLayout, eqs: list[tuple[exp.Expr, exp.Expr]], left: bool
    ) -> _Step:
        """$lookup into a root collection followed by $unwind."""
        pairs: list[tuple[str, Any, bool]] = []  # (foreign field, local expr, local nullable)
        for lhs, rhs in eqs:
            for mine, other in ((lhs, rhs), (rhs, lhs)):
                if isinstance(mine, exp.Column) and self._belongs(mine, alias):
                    col = mine
                    if not layout.has_column(_col_name(col)):
                        raise TranslationError(f"unknown column '{_col_name(col)}' in join")
                    pairs.append(
                        (layout.dotted(_col_name(col)), self._expr(other), self._nullable(other))
                    )
                    break
            else:
                raise TranslationError("each join equality must reference the joined table")
        simple = len(pairs) == 1 and not pairs[0][2] and isinstance(pairs[0][1], str)
        if simple:
            foreign, local, _ = pairs[0]
            lookup: dict[str, Any] = {
                "from": layout.collection,
                "localField": local.lstrip("$"),
                "foreignField": foreign,
                "as": alias,
            }
        else:
            lets = {f"v{i}": local for i, (_, local, _) in enumerate(pairs)}
            conds: list[Any] = []
            for i, (foreign, _, _) in enumerate(pairs):
                conds.append({"$gt": [f"$$v{i}", None]})
                conds.append({"$eq": [f"${foreign}", f"$$v{i}"]})
            lookup = {
                "from": layout.collection,
                "let": lets,
                "pipeline": [{"$match": {"$expr": {"$and": conds}}}],
                "as": alias,
            }
        return _Step(
            [
                {"$lookup": lookup},
                {"$unwind": {"path": f"${alias}", "preserveNullAndEmptyArrays": left}},
            ],
            {alias},
        )

    def _belongs(self, col: exp.Column, alias: str) -> bool:
        q = _col_qualifier(col)
        if q:
            return q == alias
        b = self.bindings[alias]
        return bool(self.ctx.schema.tables[b.table].column(_col_name(col))) and not any(
            self.ctx.schema.tables[o.table].column(_col_name(col))
            for o in self.bindings.values()
            if o.alias != alias
        )

    def _nullable(self, node: exp.Expr) -> bool:
        if not isinstance(node, exp.Column):
            return True
        b = self._binding_of(node)
        col = self.ctx.schema.tables[b.table].column(_col_name(node))
        return col is None or col.nullable

    # ---------------------------------------------------------------- resolution
    def _binding_of(self, col: exp.Column) -> _Binding:
        q = _col_qualifier(col)
        name = _col_name(col)
        if q:
            if q not in self.bindings:
                raise TranslationError(f"unknown table alias '{q}'")
            return self.bindings[q]
        owners = [b for b in self.bindings.values() if self.ctx.schema.tables[b.table].column(name)]
        if not owners:
            raise TranslationError(f"unknown column '{name}'")
        if len(owners) > 1:
            raise TranslationError(f"ambiguous column '{name}'")
        return owners[0]

    def _path(self, col: exp.Column, prefix: bool = True) -> ColRef:
        b = self._binding_of(col)
        name = _col_name(col)
        table = self.ctx.schema.tables[b.table]
        column = table.column(name)
        if column is None:
            raise TranslationError(f"unknown column '{b.alias}.{name}'")
        layout = b.layout
        if name in layout.dropped_columns:
            host_layout = self.ctx.layouts[layout.host or ""]
            host_col = layout.host_columns[layout.dropped_columns.index(name)]
            dotted = host_layout.dotted(host_col)
        else:
            dotted = layout.dotted(name)
        return ColRef(path=f"{b.ns}.{dotted}" if prefix else dotted, type=column.sql_type)

    def _expr(self, node: exp.Expr) -> Any:
        try:
            return value_expr(node, self._resolver(), None, self._hook())
        except Untranslatable as exc:
            raise TranslationError(f"cannot translate {exc.fragment}") from exc

    def _resolver(self) -> Any:
        if self.in_group:
            return self._group_resolver
        return lambda c: self._path(c)

    def _group_resolver(self, col: exp.Column) -> ColRef:
        key = self._col_key(col)
        for i, (k, _) in enumerate(self.group_exprs):
            if k == key:
                return ColRef(path=f"_id.g{i}")
        raise TranslationError(
            f"column '{_col_name(col)}' must appear in GROUP BY or be used in an aggregate"
        )

    def _col_key(self, col: exp.Column) -> Any:
        b = self._binding_of(col)
        return ("col", b.alias, _col_name(col))

    def _hook(self) -> Any:
        if not self.in_group:
            return None

        def hook(node: exp.Expr) -> Any:
            if isinstance(node, _AGG):
                return self._aggregate(node)[1]
            if not isinstance(node, exp.Column):
                key = node.sql(dialect="postgres")
                for i, (k, _) in enumerate(self.group_exprs):
                    if k == key:
                        return f"$_id.g{i}"
            return NO_HOOK

        return hook

    # ----------------------------------------------------------------- aggregates
    def _aggregate(self, node: exp.Expr) -> tuple[str, Any]:
        sql = node.sql(dialect="postgres")
        if sql in self.aggs:
            return self.aggs[sql][0], self.aggs[sql][2]
        name = f"__a{len(self.aggs)}"
        arg = node.this
        distinct = isinstance(arg, exp.Distinct)
        if distinct:
            arg = arg.expressions[0] if arg.expressions else None
        value = None if arg is None or isinstance(arg, exp.Star) else self._row_expr(arg)
        accs: dict[str, Any] = {}
        func = type(node)
        if func is exp.Count:
            if value is None:
                accs[name] = {"$sum": 1}
                proj: Any = f"${name}"
            elif distinct:
                accs[name] = {"$addToSet": {"$cond": [{"$gt": [value, None]}, value, "$$REMOVE"]}}
                proj = {"$size": f"${name}"}
            else:
                accs[name] = {"$sum": {"$cond": [{"$gt": [value, None]}, 1, 0]}}
                proj = f"${name}"
        elif func is exp.Sum:
            accs[name] = {"$sum": value}
            accs[f"{name}_n"] = {"$sum": {"$cond": [{"$gt": [value, None]}, 1, 0]}}
            proj = {"$cond": [{"$eq": [f"${name}_n", 0]}, None, f"${name}"]}
        elif func is exp.Avg:
            accs[name] = {"$avg": value}
            proj = f"${name}"
        elif func is exp.Min:
            accs[name] = {"$min": value}
            proj = f"${name}"
        else:
            accs[name] = {"$max": value}
            proj = f"${name}"
        self.aggs[sql] = (name, accs, proj)
        return name, proj

    def _row_expr(self, node: exp.Expr) -> Any:
        """Expression evaluated per input row (before grouping)."""
        saved = self.in_group
        self.in_group = False
        try:
            return self._expr(node)
        finally:
            self.in_group = saved

    # --------------------------------------------------------------------- select
    def _select_items(self) -> list[tuple[str, exp.Expr]]:
        items: list[tuple[str, exp.Expr]] = []
        used: dict[str, int] = {}

        def unique(name: str) -> str:
            n = used.get(name, 0) + 1
            used[name] = n
            return name if n == 1 else f"{name}_{n}"

        for i, e in enumerate(self.ast.expressions):
            if isinstance(e, exp.Star) or (
                isinstance(e, exp.Column) and isinstance(e.this, exp.Star)
            ):
                aliases = (
                    [_col_qualifier(e)]
                    if isinstance(e, exp.Column) and _col_qualifier(e)
                    else self.order
                )
                for a in aliases:
                    if a not in self.bindings:
                        raise TranslationError(f"unknown table alias '{a}'")
                    for col in self.ctx.schema.tables[self.bindings[a].table].columns:
                        items.append((unique(col.name), exp.column(col.name, table=a)))
                continue
            if isinstance(e, exp.Alias):
                items.append((unique(_ident(e.args["alias"])), e.this))
            elif isinstance(e, exp.Column):
                items.append((unique(_col_name(e)), e))
            elif isinstance(e, _AGG):
                items.append((unique(_AGG_NAME[type(e)]), e))
            else:
                items.append((unique(f"column{i + 1}"), e))
        return items

    def translate(self) -> tuple[str, list[Any], list[str], bool]:
        a = self.ast
        if a.args.get("distinct") is not None and not isinstance(a.args["distinct"], exp.Distinct):
            pass
        self.build_scope()
        self.select_items = self._select_items()
        outputs = [n for n, _ in self.select_items]
        group = a.args.get("group")
        having = a.args.get("having")
        is_agg = (
            group is not None
            or having is not None
            or any(has_aggregate(e) for _, e in self.select_items)
        )
        stages: list[Any] = []
        where = a.args.get("where")
        self._place_where(where.this if where is not None else None, stages)
        order_items = list(a.args["order"].expressions) if a.args.get("order") is not None else []
        limit = self._int_arg(a.args.get("limit"))
        offset = self._int_arg(a.args.get("offset"))
        distinct = a.args.get("distinct") is not None
        if is_agg:
            self._agg_flow(stages, group, having, order_items, outputs)
        elif distinct:
            self._project_flow(stages, outputs)
            stages.append({"$group": {"_id": {n: f"${n}" for n in outputs}}})
            stages.append({"$replaceRoot": {"newRoot": "$_id"}})
            self._sort_stage(stages, order_items, outputs, post=True)
        else:
            self._sort_stage(stages, order_items, outputs, post=False)
        if offset:
            stages.append({"$skip": offset})
        if limit is not None:
            stages.append({"$limit": limit})
        if not distinct and not is_agg:
            self._project_flow(stages, outputs)
        else:
            stages.append({"$project": {"_id": 0, **{n: 1 for n in outputs}}})
        self.order_positions = self._order_positions(order_items, outputs)
        self.global_aggregate = is_agg and group is None
        return self.source_collection, stages, outputs, bool(order_items)

    def _order_positions(self, order_items: list[exp.Expr], outputs: list[str]) -> list[int] | None:
        positions: list[int] = []
        for item in order_items:
            node = item.this if isinstance(item, exp.Ordered) else item
            pos: int | None = None
            if isinstance(node, exp.Literal) and not node.is_string:
                pos = int(node.this) - 1
            elif (
                isinstance(node, exp.Column)
                and not _col_qualifier(node)
                and _col_name(node) in outputs
            ):
                pos = outputs.index(_col_name(node))
            if pos is None:
                key = node.sql(dialect="postgres")
                for i, (_, e) in enumerate(self.select_items):
                    if e.sql(dialect="postgres") == key:
                        pos = i
                        break
                    if isinstance(node, exp.Column) and isinstance(e, exp.Column):
                        try:
                            if self._col_key(node) == self._col_key(e):
                                pos = i
                                break
                        except TranslationError:
                            pass
            if pos is None:
                return None
            positions.append(pos)
        return positions

    @staticmethod
    def _int_arg(node: exp.Expr | None) -> int | None:
        if node is None:
            return None
        lit = node.args.get("expression")
        if isinstance(lit, exp.Literal) and not lit.is_string:
            return int(lit.this)
        raise TranslationError("LIMIT/OFFSET must be integer literals")

    def _project_flow(self, stages: list[Any], outputs: list[str]) -> None:
        proj: dict[str, Any] = {"_id": 0}
        for name, e in self.select_items:
            value = self._expr(e)
            proj[name] = {"$ifNull": [value, None]} if not _is_literal_value(value) else value
        stages.append({"$project": proj})

    # --------------------------------------------------------------------- where
    def _place_where(self, where: exp.Expr | None, stages: list[Any]) -> None:
        terms = conjuncts(where) if where is not None else []
        pushdown: list[Any] = []
        by_step: dict[int, list[Any]] = {}
        for term in terms:
            aliases = self._aliases_in(term)
            try:
                pred = parse_predicate(term)
            except Untranslatable as exc:
                raise TranslationError(f"cannot translate WHERE term: {exc.fragment}") from exc
            push = bool(aliases) and all(
                self.bindings[a].layout.is_root and self.bindings[a].ns == self.source_ns
                for a in aliases
                if a in self.bindings
            )
            try:
                if push:
                    flt = to_query(pred, lambda c: self._path(c, prefix=False))
                    pushdown.append(flt)
                    continue
                flt = to_query(pred, lambda c: self._path(c))
            except Untranslatable as exc:
                raise TranslationError(f"cannot translate WHERE term: {exc.fragment}") from exc
            idx = max((self._avail(a) for a in aliases), default=-1)
            by_step.setdefault(idx, []).append(flt)
        merged = [f for f in pushdown if f]
        if merged:
            stages.append({"$match": merged[0] if len(merged) == 1 else {"$and": merged}})
        stages.append({"$replaceRoot": {"newRoot": {self.source_ns: "$$ROOT"}}})
        for f in by_step.get(-1, []):
            if f:
                stages.append({"$match": f})
        for i, step in enumerate(self.steps):
            if step.stage is not None:
                stages.extend(step.stage if isinstance(step.stage, list) else [step.stage])
            for f in by_step.get(i, []):
                if f:
                    stages.append({"$match": f})

    def _avail(self, alias: str) -> int:
        idx = -1
        for i, s in enumerate(self.steps):
            if alias in s.provides:
                idx = i
        return idx

    # ---------------------------------------------------------------- group / agg
    def _agg_flow(
        self,
        stages: list[Any],
        group: exp.Expr | None,
        having: exp.Expr | None,
        order_items: list[exp.Expr],
        outputs: list[str],
    ) -> None:
        # group keys
        key_nodes: list[exp.Expr] = list(group.expressions) if group is not None else []
        resolved: list[exp.Expr] = []
        for g in key_nodes:
            resolved.append(self._resolve_group_item(g))
        for g in resolved:
            key = self._col_key(g) if isinstance(g, exp.Column) else g.sql(dialect="postgres")
            self.group_exprs.append((key, self._row_expr(g)))
        # collect aggregates from select, having and order by before building $group
        self.in_group = True
        for _, e in self.select_items:
            for node in e.find_all(*_AGG):
                self._aggregate(node)
        if having is not None:
            for node in having.find_all(*_AGG):
                self._aggregate(node)
        for item in order_items:
            for node in item.find_all(*_AGG):
                self._aggregate(node)
        accumulators: dict[str, Any] = {}
        for _, accs, _ in self.aggs.values():
            accumulators.update(accs)
        group_id: Any = {f"g{i}": expr for i, (_, expr) in enumerate(self.group_exprs)} or None
        stages.append({"$group": {"_id": group_id, **accumulators}})
        add: dict[str, Any] = {}
        for name, e in self.select_items:
            add[name] = {"$ifNull": [self._expr(e), None]}
        stages.append({"$addFields": add})
        if having is not None:
            try:
                pred = parse_predicate(having.this)
                cond = to_expr(pred, self._group_resolver, "where", self._hook())
            except Untranslatable as exc:
                raise TranslationError(f"cannot translate HAVING: {exc.fragment}") from exc
            stages.append({"$match": {"$expr": cond}})
        self._sort_stage(stages, order_items, outputs, post=True)

    def _resolve_group_item(self, g: exp.Expr) -> exp.Expr:
        if isinstance(g, exp.Literal) and not g.is_string:
            idx = int(g.this) - 1
            if not 0 <= idx < len(self.select_items):
                raise TranslationError(f"GROUP BY position {g.this} is not in the select list")
            return self.select_items[idx][1]
        if isinstance(g, exp.Column) and not _col_qualifier(g):
            name = _col_name(g)
            if not any(
                self.ctx.schema.tables[b.table].column(name) for b in self.bindings.values()
            ):
                for out, e in self.select_items:
                    if out == name:
                        return e
        return g

    # ----------------------------------------------------------------------- sort
    def _sort_stage(
        self, stages: list[Any], order_items: list[exp.Expr], outputs: list[str], post: bool
    ) -> None:
        if not order_items:
            return
        add: dict[str, Any] = {}
        sort: dict[str, int] = {}
        for i, item in enumerate(order_items):
            assert isinstance(item, exp.Ordered)
            node = item.this
            desc = bool(item.args.get("desc"))
            nulls_first = item.args.get("nulls_first")
            nulls_first = desc if nulls_first is None else bool(nulls_first)
            key_expr, key_field = self._order_key(node, outputs, post, i, add)
            add[f"_n{i}"] = {"$cond": [{"$lte": [key_expr, None]}, 1, 0]}
            sort[f"_n{i}"] = -1 if nulls_first else 1
            sort[key_field] = -1 if desc else 1
        stages.append({"$addFields": add})
        stages.append({"$sort": sort})

    def _order_key(
        self, node: exp.Expr, outputs: list[str], post: bool, i: int, add: dict[str, Any]
    ) -> tuple[Any, str]:
        if isinstance(node, exp.Literal) and not node.is_string:
            idx = int(node.this) - 1
            if not 0 <= idx < len(outputs):
                raise TranslationError(f"ORDER BY position {node.this} is not in the select list")
            return f"${outputs[idx]}", outputs[idx]
        if isinstance(node, exp.Column) and not _col_qualifier(node):
            name = _col_name(node)
            real = any(self.ctx.schema.tables[b.table].column(name) for b in self.bindings.values())
            if name in outputs and (post or not real):
                return f"${name}", name
        key = node.sql(dialect="postgres")
        for out, e in self.select_items:
            if e.sql(dialect="postgres") == key and post:
                return f"${out}", out
        if isinstance(node, exp.Column) and not self.in_group:
            ref = self._path(node)
            return f"${ref.path}", ref.path
        value = self._expr(node)
        add[f"_k{i}"] = value
        return f"$_k{i}", f"_k{i}"


def _is_literal_value(v: Any) -> bool:
    return not isinstance(v, (str, dict)) or (isinstance(v, str) and not v.startswith("$"))


# =================================================================== DML translation
def and_filters(*parts: dict[str, Any]) -> dict[str, Any]:
    """AND several match documents, merging them when their keys do not collide."""
    parts = tuple(p for p in parts if p)
    merged: dict[str, Any] = {}
    for part in parts:
        if any(k in merged or k.startswith("$") for k in part):
            return {"$and": list(parts)} if len(parts) > 1 else parts[0]
        merged.update(part)
    return merged


def positional_path(
    chain: list[TableLayout], last_ident: str | None, last_filter: dict[str, Any] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Update path through nested embedded arrays with named positional filters.

    Outer arrays use ``$[oN]`` guarded by an ``$exists`` array filter (a bare ``$[]`` fails when
    some element lacks the inner array); the last array uses ``$[last_ident]`` with ``last_filter``.
    """
    parts: list[str] = []
    filters: list[dict[str, Any]] = []
    for i, lay in enumerate(chain):
        parts.append(lay.path[-1])
        if lay.kind not in ("array", "ref_docs"):
            continue
        if i == len(chain) - 1:
            if last_ident:
                parts.append(f"$[{last_ident}]")
                if last_filter:
                    filters.append(last_filter)
        else:
            ident = f"o{i}"
            parts.append(f"$[{ident}]")
            filters.append({f"{ident}.{chain[i + 1].path[-1]}": {"$exists": True}})
    return ".".join(parts), filters


class _Dml:
    def __init__(self, q: Query, ctx: QueryContext) -> None:
        self.q, self.ctx = q, ctx
        self.ast = q.ast

    def _target(self, node: exp.Expr | None) -> tuple[str, Table, TableLayout]:
        if isinstance(node, exp.Schema):
            node = node.this
        if not isinstance(node, exp.Table):
            raise TranslationError("unsupported statement target")
        name = _table_name(node)
        if name not in self.ctx.schema.tables:
            raise TranslationError(f"unknown table '{name}'")
        return name, self.ctx.schema.tables[name], self.ctx.layouts[name]

    # ------------------------------------------------------------------- helpers
    def _row_doc(self, table: Table, layout: TableLayout, row: dict[str, Any]) -> dict[str, Any]:
        """Document for one row, omitting NULLs; fills constant/now defaults."""
        doc: dict[str, Any] = {}
        for col in table.columns:
            if col.name in layout.dropped_columns:
                continue
            if col.name not in row:
                value = self._default(col)
                if value is None:
                    continue
            else:
                value = row[col.name]
            if value is None:
                continue
            if col.name == layout.scalar_column:
                continue
            doc[layout.fields.get(col.name, col.name)] = value
        return doc

    def _default(self, col: Column) -> Any:
        if col.default is None:
            return None
        if col.default.kind == "now":
            return NowValue()
        if col.default.kind == "literal":
            import sqlglot

            return typed_value(sqlglot.parse_one(col.default.sql, dialect="postgres"), col)
        return None

    def _host_filter(
        self, layout: TableLayout, row: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        """Host document filter, arrayFilters and the update path of this table."""
        host = self.ctx.layouts[layout.host or ""]
        key_cols = layout.dropped_columns
        if any(c not in row for c in key_cols):
            raise TranslationError(
                f"INSERT into {layout.table} (embedded in {layout.host}) must supply "
                f"{', '.join(key_cols)} to locate the parent"
            )
        flt: dict[str, Any] = {}
        for hc, kc in zip(layout.host_columns, key_cols, strict=True):
            flt[host.dotted(hc)] = row[kc]
        chain = self._chain(layout)
        host_chain = chain[:-1]
        host_filter = {
            f"h.{host.fields[hc]}": row[kc]
            for hc, kc in zip(layout.host_columns, key_cols, strict=True)
        }
        if host_chain:
            prefix, filters = positional_path(host_chain, "h", host_filter)
            return flt, filters, f"{prefix}.{layout.path[-1]}"
        return flt, [], layout.path[-1]

    def _chain(self, layout: TableLayout) -> list[TableLayout]:
        chain: list[TableLayout] = []
        cur: TableLayout | None = layout
        while cur is not None and not cur.is_root:
            chain.append(cur)
            cur = self.ctx.layouts.get(cur.host or "")
        return list(reversed(chain))

    # -------------------------------------------------------------------- INSERT
    def insert(self) -> tuple[str, list[WriteOp], list[str]]:
        ast = self.ast
        name, table, layout = self._target(ast.this)
        cols = [
            _ident(i) for i in (ast.this.expressions if isinstance(ast.this, exp.Schema) else [])
        ] or [c.name for c in table.columns]
        values = ast.args["expression"]
        rows: list[dict[str, Any]] = []
        for tup in values.expressions:
            items = tup.expressions if isinstance(tup, exp.Tuple) else [tup]
            if len(items) != len(cols):
                raise TranslationError("INSERT column count does not match VALUES")
            row = {}
            for cname, node in zip(cols, items, strict=True):
                col = table.column(cname)
                if col is None:
                    raise TranslationError(f"unknown column '{cname}'")
                row[cname] = typed_value(node, col)
            rows.append(row)
        notes: list[str] = []
        for col in table.columns:
            if col.is_identity and all(col.name not in r for r in rows):
                notes.append(
                    f"{table.name}.{col.name} is auto-generated: allocated by the insert helper "
                    "(counter or ObjectId), not by this statement"
                )
        ops: list[WriteOp] = []
        if layout.is_root:
            docs = [self._row_doc(table, layout, r) for r in rows]
            ops.append(
                WriteOp(
                    op="insertOne" if len(docs) == 1 else "insertMany",
                    collection=layout.collection,
                    documents=docs,
                )
            )
            return layout.collection, ops, notes
        for r in rows:
            flt, afs, path = self._host_filter(layout, r)
            if layout.kind == "ref_scalars":
                element = r[layout.scalar_column or ""]
                update: dict[str, Any] = {"$addToSet": {path: element}}
            elif layout.kind == "object":
                update = {"$set": {path: self._row_doc(table, layout, r)}}
            else:
                update = {"$push": {path: self._row_doc(table, layout, r)}}
            ops.append(
                WriteOp(
                    op="updateOne",
                    collection=layout.collection,
                    filter=flt,
                    update=update,
                    array_filters=afs or None,
                )
            )
        return layout.collection, ops, notes

    # -------------------------------------------------------------------- UPDATE
    def update(self) -> tuple[str, list[WriteOp], list[str]]:
        ast = self.ast
        name, table, layout = self._target(ast.this)
        sets: dict[str, exp.Expr] = {}
        for eq in ast.expressions:
            if not isinstance(eq, exp.EQ) or not isinstance(eq.this, exp.Column):
                raise TranslationError("unsupported SET clause")
            cname = _col_name(eq.this)
            if table.column(cname) is None:
                raise TranslationError(f"unknown column '{cname}'")
            if cname == layout.id_column:
                raise TranslationError(
                    f"{name}.{cname} is stored as the immutable _id and cannot be updated"
                )
            if cname in layout.dropped_columns:
                raise TranslationError(
                    f"{name}.{cname} is implied by nesting inside {layout.host}; "
                    "move the document instead of updating the key"
                )
            sets[cname] = eq.expression
        where = ast.args.get("where")
        terms = conjuncts(where.this) if where is not None else []
        if layout.is_root:
            return self._update_root(table, layout, sets, terms)
        return self._update_embedded(table, layout, sets, terms)

    def _resolver(self, table: Table, layout: TableLayout, prefix: str = "") -> Any:
        def resolve(c: exp.Column) -> ColRef:
            cname = _col_name(c)
            col = table.column(cname)
            if col is None or cname not in layout.fields:
                raise TranslationError(f"unknown or implied column '{cname}'")
            return ColRef(path=prefix + layout.fields[cname], type=col.sql_type)

        return resolve

    def _where_filter(self, terms: list[exp.Expr], resolve: Any) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for t in terms:
            try:
                f = to_query(parse_predicate(t), resolve)
            except Untranslatable as exc:
                raise TranslationError(f"cannot translate WHERE: {exc.fragment}") from exc
            if f:
                parts.append(f)
        if not parts:
            return {}
        return parts[0] if len(parts) == 1 else {"$and": parts}

    def _set_doc(
        self, table: Table, layout: TableLayout, sets: dict[str, exp.Expr], prefix: str = ""
    ) -> tuple[Any, bool]:
        """(update document, is_pipeline). Column references force a pipeline update."""
        uses_columns = any(list(e.find_all(exp.Column)) for e in sets.values())
        resolve = self._resolver(table, layout, "")
        if uses_columns:
            pipeline_set: dict[str, Any] = {}
            for cname, node in sets.items():
                target = table.column(cname)
                value = value_expr(node, resolve, target.sql_type if target else None)
                pipeline_set[prefix + layout.fields[cname]] = (
                    "$$REMOVE" if isinstance(node, exp.Null) else value
                )
            return [{"$set": pipeline_set}], True
        set_doc: dict[str, Any] = {}
        unset: dict[str, str] = {}
        for cname, node in sets.items():
            value = typed_value(node, table.column(cname))
            field_path = prefix + layout.fields[cname]
            if value is None:
                unset[field_path] = ""
            else:
                set_doc[field_path] = value
        update: dict[str, Any] = {}
        if set_doc:
            update["$set"] = set_doc
        if unset:
            update["$unset"] = unset
        return update, False

    def _update_root(
        self, table: Table, layout: TableLayout, sets: dict[str, exp.Expr], terms: list[exp.Expr]
    ) -> tuple[str, list[WriteOp], list[str]]:
        flt = self._where_filter(terms, self._resolver(table, layout))
        update, _ = self._set_doc(table, layout, sets)
        return (
            layout.collection,
            [WriteOp(op="updateMany", collection=layout.collection, filter=flt, update=update)],
            [],
        )

    def _split_terms(
        self, layout: TableLayout, terms: list[exp.Expr]
    ) -> tuple[list[exp.Expr], list[exp.Expr]]:
        """(own-column terms, terms on the columns implied by nesting = host key)."""
        own: list[exp.Expr] = []
        host_terms: list[exp.Expr] = []
        for t in terms:
            cols = [_col_name(c) for c in t.find_all(exp.Column)]
            (host_terms if cols and all(c in layout.dropped_columns for c in cols) else own).append(
                t
            )
        return own, host_terms

    def _host_key_filter(self, layout: TableLayout, host_terms: list[exp.Expr]) -> dict[str, Any]:
        host = self.ctx.layouts[layout.host or ""]

        def host_resolve(c: exp.Column) -> ColRef:
            hc = layout.host_columns[layout.dropped_columns.index(_col_name(c))]
            return ColRef(path=host.dotted(hc), type=None)

        return self._where_filter(host_terms, host_resolve) if host_terms else {}

    def _update_embedded(
        self, table: Table, layout: TableLayout, sets: dict[str, exp.Expr], terms: list[exp.Expr]
    ) -> tuple[str, list[WriteOp], list[str]]:
        if layout.kind == "ref_scalars":
            raise TranslationError(
                f"{table.name} is a folded junction without payload: nothing to update"
            )
        if any(list(e.find_all(exp.Column)) for e in sets.values()):
            raise TranslationError(
                f"{table.name} is embedded in {layout.host}: SET values must be literals"
            )
        chain = self._chain(layout)
        own_terms, host_terms = self._split_terms(layout, terms)
        flt = self._host_key_filter(layout, host_terms)
        if layout.is_array:
            element_filter = self._where_filter(own_terms, self._resolver(table, layout, "el."))
            path, filters = positional_path(
                chain, "el", element_filter or {"el": {"$exists": True}}
            )
            update, _ = self._set_doc(table, layout, sets, prefix=path + ".")
            # a narrowing hint for the host query (not required for correctness)
            dotted = ".".join(c.path[-1] for c in chain)
            hint = self._where_filter(own_terms, self._resolver(table, layout, dotted + "."))
            flt = and_filters(flt, hint, {dotted: {"$exists": True}})
            return (
                layout.collection,
                [
                    WriteOp(
                        op="updateMany",
                        collection=layout.collection,
                        filter=flt,
                        update=update,
                        array_filters=filters,
                    )
                ],
                [],
            )
        path, filters = positional_path(chain, None)
        own_filter = self._where_filter(own_terms, self._resolver(table, layout, path + "."))
        flt = and_filters(flt, own_filter, {path: {"$exists": True}})
        update, _ = self._set_doc(table, layout, sets, prefix=path + ".")
        return (
            layout.collection,
            [
                WriteOp(
                    op="updateMany",
                    collection=layout.collection,
                    filter=flt,
                    update=update,
                    array_filters=filters or None,
                )
            ],
            [],
        )

    # -------------------------------------------------------------------- DELETE
    def delete(self) -> tuple[str, list[WriteOp], list[str]]:
        ast = self.ast
        name, table, layout = self._target(ast.this)
        where = ast.args.get("where")
        terms = conjuncts(where.this) if where is not None else []
        if layout.is_root:
            flt = self._where_filter(terms, self._resolver(table, layout))
            return (
                layout.collection,
                [WriteOp(op="deleteMany", collection=layout.collection, filter=flt)],
                [
                    f"deleting from {name} does not cascade: use the generated "
                    f"delete_{name}() helper"
                ],
            )
        chain = self._chain(layout)
        own_terms, host_terms = self._split_terms(layout, terms)
        host_filter = self._host_key_filter(layout, host_terms)
        path, filters = positional_path(chain, None)
        if layout.kind == "ref_scalars":
            if len(own_terms) != 1 or not isinstance(own_terms[0], exp.EQ):
                raise TranslationError(
                    f"DELETE on {name} needs `{layout.scalar_column} = <value>` in WHERE"
                )
            eq = own_terms[0]
            col_node, val = (
                (eq.this, eq.expression)
                if isinstance(eq.this, exp.Column)
                else (eq.expression, eq.this)
            )
            update: dict[str, Any] = {
                "$pull": {path: typed_value(val, table.column(_col_name(col_node)))}
            }
        elif layout.kind == "object":
            update = {"$unset": {path: ""}}
        else:
            update = {
                "$pull": {path: self._where_filter(own_terms, self._resolver(table, layout)) or {}}
            }
        dotted = ".".join(c.path[-1] for c in chain)
        return (
            layout.collection,
            [
                WriteOp(
                    op="updateMany",
                    collection=layout.collection,
                    filter=and_filters(host_filter, {dotted: {"$exists": True}}),
                    update=update,
                    array_filters=filters or None,
                )
            ],
            [f"deleting from embedded {name} does not cascade to anything stored elsewhere"],
        )


# ======================================================================== entry points
def translate_query(
    q: Query, ctx: QueryContext, transaction_id: str | None = None
) -> TranslatedQuery:
    base = {
        "query_id": q.id,
        "sql": one_line(q.raw_sql),
        "kind": q.kind,
        "transaction_id": transaction_id,
    }
    try:
        if q.kind == "SELECT":
            sel = _Select(q, ctx)
            collection, pipeline, outputs, ordered = sel.translate()
            return TranslatedQuery(
                **base,
                collection=collection,
                pipeline=pipeline,
                output_columns=outputs,
                ordered=ordered,
                order_positions=sel.order_positions,
                global_aggregate=sel.global_aggregate,
                notes=sel.notes,
            )
        dml = _Dml(q, ctx)
        if q.kind == "INSERT":
            coll, ops, notes = dml.insert()
        elif q.kind == "UPDATE":
            coll, ops, notes = dml.update()
        else:
            coll, ops, notes = dml.delete()
        return TranslatedQuery(**base, collection=coll, operations=ops, notes=notes)
    except (TranslationError, Untranslatable) as exc:
        message = str(exc) if isinstance(exc, TranslationError) else f"cannot translate {exc}"
        return TranslatedQuery(**base, error=message)


def translate_all(
    queries: tuple[Query | TransactionBlock, ...], ctx: QueryContext
) -> list[TranslatedQuery]:
    out: list[TranslatedQuery] = []
    for item in queries:
        if isinstance(item, TransactionBlock):
            out.extend(translate_query(s, ctx, item.id) for s in item.statements)
        else:
            out.append(translate_query(item, ctx))
    return out


_ = (RawJs, re, NormalizedType)
