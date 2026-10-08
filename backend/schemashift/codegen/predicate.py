"""SQL predicate -> MongoDB translation with SQL three-valued-logic semantics.

Two consumers need different NULL behaviour:
- ``where``: a row passes only when the predicate is TRUE (unknown rows are filtered out);
- ``check``: a row passes unless the predicate is definitely FALSE (CHECK constraints accept NULLs).

NOT is pushed to the leaves first (negation normal form), so a leaf only has to answer
"true", "false" or "unknown because an operand is NULL". SQL NULL is represented in MongoDB by
an *absent* field, which both ``{f: null}`` queries and ``$gt: [x, null]`` expressions
treat as null.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlglot import exp

from schemashift.codegen.values import DateValue, RegexValue, UuidValue
from schemashift.models.schema import NormalizedType

Mode = Literal["where", "check"]


class Untranslatable(Exception):
    """Raised with the SQL fragment that has no MongoDB equivalent."""

    def __init__(self, fragment: str) -> None:
        super().__init__(f"cannot translate: {fragment}")
        self.fragment = fragment


@dataclass(frozen=True)
class ColRef:
    """A resolved column: its path (``$``-prefixed expression form) and SQL type."""

    path: str  # dotted path without a leading "$", relative to ``var``
    type: NormalizedType | None = None
    var: str = "$"  # "$" for the document, "$$el." inside array-element lambdas

    @property
    def expr(self) -> str:
        return f"{self.var}{self.path}"


Resolver = Callable[[exp.Column], ColRef]

# ---------------------------------------------------------------------- predicate tree


@dataclass(frozen=True)
class PAnd:
    items: tuple[Pred, ...]


@dataclass(frozen=True)
class POr:
    items: tuple[Pred, ...]


@dataclass(frozen=True)
class PCmp:
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    left: exp.Expr
    right: exp.Expr


@dataclass(frozen=True)
class PIn:
    left: exp.Expr
    items: tuple[exp.Expr, ...]
    neg: bool


@dataclass(frozen=True)
class PLike:
    left: exp.Expr
    pattern: exp.Expr
    neg: bool
    ci: bool


@dataclass(frozen=True)
class PNull:
    left: exp.Expr
    neg: bool  # True = IS NOT NULL


@dataclass(frozen=True)
class PBool:
    expr: exp.Expr
    value: bool  # require the boolean expression to equal this


@dataclass(frozen=True)
class PConst:
    value: bool | None  # None = SQL NULL (unknown)


Pred = PAnd | POr | PCmp | PIn | PLike | PNull | PBool | PConst

_CMP_NEGATE = {"eq": "ne", "ne": "eq", "lt": "gte", "gte": "lt", "lte": "gt", "gt": "lte"}
_CMP_OF: dict[type[exp.Expr], str] = {
    exp.EQ: "eq",
    exp.NEQ: "ne",
    exp.LT: "lt",
    exp.LTE: "lte",
    exp.GT: "gt",
    exp.GTE: "gte",
}
_CMP_MQL = {"eq": "$eq", "ne": "$ne", "lt": "$lt", "lte": "$lte", "gt": "$gt", "gte": "$gte"}


def parse_predicate(node: exp.Expr, negate: bool = False) -> Pred:
    """Build the predicate tree in negation normal form."""
    if isinstance(node, exp.Paren):
        return parse_predicate(node.this, negate)
    if isinstance(node, exp.Not):
        return parse_predicate(node.this, not negate)
    if isinstance(node, (exp.And, exp.Or)):
        parts = (parse_predicate(node.this, negate), parse_predicate(node.expression, negate))
        conj = isinstance(node, exp.And) != negate
        return PAnd(parts) if conj else POr(parts)
    for cls, op in _CMP_OF.items():
        if type(node) is cls:
            real = _CMP_NEGATE[op] if negate else op
            return PCmp(real, node.this, node.expression)  # type: ignore[arg-type]
    if isinstance(node, (exp.In, exp.Between, exp.Like, exp.ILike, exp.Is)):
        negate = negate != bool(
            node.args.get("negate")
        )  # sqlglot stores NOT IN / IS NOT / NOT LIKE
    if isinstance(node, exp.In):
        if node.args.get("query") is not None:
            raise Untranslatable(node.sql(dialect="postgres"))
        return PIn(node.this, tuple(node.expressions), negate)
    if isinstance(node, exp.Between):
        low, high = node.args["low"], node.args["high"]
        if negate:
            return POr((PCmp("lt", node.this, low), PCmp("gt", node.this, high)))
        return PAnd((PCmp("gte", node.this, low), PCmp("lte", node.this, high)))
    if isinstance(node, (exp.Like, exp.ILike)):
        return PLike(node.this, node.expression, negate, isinstance(node, exp.ILike))
    if isinstance(node, exp.Is):
        if isinstance(node.expression, exp.Null):
            return PNull(node.this, negate)
        raise Untranslatable(node.sql(dialect="postgres"))
    if isinstance(node, exp.Boolean):
        return PConst(bool(node.this) != negate)
    if isinstance(node, exp.Null):
        return PConst(None)
    if isinstance(node, exp.Column):
        return PBool(node, not negate)
    raise Untranslatable(node.sql(dialect="postgres"))


# ---------------------------------------------------------------------------- literals
def like_to_regex(pattern: str, escape: str = "\\") -> str:
    """SQL LIKE pattern -> anchored regular expression (use with the ``s`` option)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == escape and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return "^" + "".join(out) + "$"


def parse_timestamp(text: str) -> str:
    """Normalise a SQL date/timestamp string to an ISO-8601 UTC string."""
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def literal_value(node: exp.Expr, hint: NormalizedType | None) -> Any:
    """Python value for a SQL literal, converted to the BSON type of the compared column."""
    if isinstance(node, exp.Paren):
        return literal_value(node.this, hint)
    if isinstance(node, exp.Neg):
        inner = literal_value(node.this, hint)
        if isinstance(inner, (int, float)):
            return -inner
        raise Untranslatable(node.sql(dialect="postgres"))
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Literal):
        if node.is_string:
            text = str(node.this)
            if hint is not None and hint.base in ("DATE", "TIMESTAMP", "TIMESTAMPTZ"):
                try:
                    return DateValue(parse_timestamp(text))
                except ValueError as exc:
                    raise Untranslatable(node.sql(dialect="postgres")) from exc
            if hint is not None and hint.base == "UUID" and not hint.is_array:
                return UuidValue(text)
            return text
        text = str(node.this)
        number: int | float = int(text) if re.fullmatch(r"-?\d+", text) else float(text)
        if hint is not None and hint.base in ("FLOAT", "DOUBLE") and isinstance(number, int):
            return float(number)
        return number
    raise Untranslatable(node.sql(dialect="postgres"))


def is_literal(node: exp.Expr) -> bool:
    if isinstance(node, (exp.Paren, exp.Neg)):
        return is_literal(node.this)
    return isinstance(node, (exp.Literal, exp.Boolean, exp.Null))


def _hint_for(*nodes: exp.Expr, resolve: Resolver) -> NormalizedType | None:
    for n in nodes:
        if isinstance(n, exp.Column):
            return resolve(n).type
    return None


# ------------------------------------------------------------------- scalar expressions
NO_HOOK: Any = object()
Hook = Callable[[exp.Expr], Any]


def value_expr(
    node: exp.Expr,
    resolve: Resolver,
    hint: NormalizedType | None = None,
    hook: Hook | None = None,
) -> Any:
    """Aggregation expression for a scalar SQL expression.

    ``hook`` may claim a node (aggregates, group keys) by returning anything but ``NO_HOOK``.
    """
    if hook is not None:
        claimed = hook(node)
        if claimed is not NO_HOOK:
            return claimed
    if isinstance(node, exp.Paren):
        return value_expr(node.this, resolve, hint, hook)
    if isinstance(node, exp.Column):
        return resolve(node).expr
    if is_literal(node):
        value = literal_value(node, hint)
        if isinstance(value, str) and value.startswith("$"):
            return {"$literal": value}
        return value
    binary = {exp.Add: "$add", exp.Sub: "$subtract", exp.Mul: "$multiply", exp.Div: "$divide"}
    for cls, op in binary.items():
        if type(node) is cls:
            inner_hint = hint or _hint_for(node.this, node.expression, resolve=resolve)
            return {
                op: [
                    value_expr(node.this, resolve, inner_hint, hook),
                    value_expr(node.expression, resolve, inner_hint, hook),
                ]
            }
    if isinstance(node, exp.Neg):
        return {"$multiply": [-1, value_expr(node.this, resolve, hint, hook)]}
    if isinstance(node, exp.Length):
        return {"$strLenCP": value_expr(node.this, resolve, None, hook)}
    raise Untranslatable(node.sql(dialect="postgres"))


def _notnull(x: Any) -> dict[str, Any]:
    return {"$gt": [x, None]}


def _isnull(x: Any) -> dict[str, Any]:
    return {"$lte": [x, None]}


# ---------------------------------------------------------------------- expression form
def to_expr(pred: Pred, resolve: Resolver, mode: Mode, hook: Hook | None = None) -> Any:
    """Boolean aggregation expression (for ``$expr`` / ``$match: {$expr}`` / validators)."""
    unknown_passes = mode == "check"
    if isinstance(pred, PConst):
        if pred.value is None:
            return unknown_passes
        return pred.value
    if isinstance(pred, PAnd):
        return {"$and": [to_expr(p, resolve, mode, hook) for p in pred.items]}
    if isinstance(pred, POr):
        return {"$or": [to_expr(p, resolve, mode, hook) for p in pred.items]}
    if isinstance(pred, PCmp):
        hint = _hint_for(pred.left, pred.right, resolve=resolve)
        left = value_expr(pred.left, resolve, hint, hook)
        right = value_expr(pred.right, resolve, hint, hook)
        core = {_CMP_MQL[pred.op]: [left, right]}
        operands = [(pred.left, left), (pred.right, right)]
        if any(isinstance(n, exp.Null) for n, _ in operands):
            return unknown_passes
        checked = [x for n, x in operands if not is_literal(n)]
        if unknown_passes:
            guards = [_isnull(x) for x in checked]
            return {"$or": [*guards, core]} if guards else core
        guards = [_notnull(x) for x in checked]
        return {"$and": [*guards, core]} if guards else core
    if isinstance(pred, PIn):
        hint = _hint_for(pred.left, resolve=resolve)
        left = value_expr(pred.left, resolve, None, hook)
        values = [literal_value(i, hint) for i in pred.items]
        has_null = any(v is None for v in values)
        values = [v for v in values if v is not None]
        membership: Any = {"$in": [left, values]}
        if pred.neg:
            if has_null:  # x NOT IN (..., NULL) is never TRUE
                return unknown_passes and {"$or": [_isnull(left), {"$not": [membership]}]}
            membership = {"$not": [membership]}
        guard = _isnull(left) if unknown_passes else _notnull(left)
        return {"$or": [guard, membership]} if unknown_passes else {"$and": [guard, membership]}
    if isinstance(pred, PLike):
        left = value_expr(pred.left, resolve, None, hook)
        regex = _like_regex(pred.pattern)
        match: Any = {
            "$regexMatch": {"input": left, "regex": regex, "options": "si" if pred.ci else "s"}
        }
        if pred.neg:
            match = {"$not": [match]}
        if unknown_passes:
            return {"$or": [_isnull(left), match]}
        return {"$and": [_notnull(left), match]} if pred.neg else match
    if isinstance(pred, PNull):
        left = value_expr(pred.left, resolve, None, hook)
        return _notnull(left) if pred.neg else _isnull(left)
    if isinstance(pred, PBool):
        x = value_expr(pred.expr, resolve, None, hook)
        if unknown_passes:
            return {"$ne": [x, not pred.value]}
        return {"$eq": [x, pred.value]}
    raise Untranslatable(str(pred))


def _like_regex(pattern: exp.Expr) -> str:
    if isinstance(pattern, exp.Literal) and pattern.is_string:
        return like_to_regex(str(pattern.this))
    raise Untranslatable(pattern.sql(dialect="postgres"))


# ----------------------------------------------------------------------------- query form
def to_query(pred: Pred, resolve: Resolver) -> dict[str, Any]:
    """``$match`` filter in query-operator form (falls back to ``$expr`` per leaf).

    Only used for ``where`` mode: leaves never match when an operand is NULL.
    """
    if isinstance(pred, PConst):
        return {} if pred.value else {"$expr": False}
    if isinstance(pred, PAnd):
        return _combine("$and", [to_query(p, resolve) for p in pred.items])
    if isinstance(pred, POr):
        return {"$or": [to_query(p, resolve) for p in pred.items]}
    simple = _simple_leaf(pred, resolve)
    if simple is not None:
        return simple
    return {"$expr": to_expr(pred, resolve, "where")}


def _combine(op: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    parts = [p for p in parts if p]
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {op: parts}


def _column(node: exp.Expr) -> exp.Column | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node if isinstance(node, exp.Column) else None


def _simple_leaf(pred: Pred, resolve: Resolver) -> dict[str, Any] | None:
    if isinstance(pred, PCmp):
        col, other, op = _column(pred.left), pred.right, pred.op
        if col is None:
            col, other, op = _column(pred.right), pred.left, _flip(pred.op)  # type: ignore[assignment]
        if col is None or not is_literal(other) or isinstance(_strip(other), exp.Null):
            return None
        ref = resolve(col)
        value = literal_value(other, ref.type)
        if op == "eq":
            return {ref.path: value}
        if op == "ne":
            return {ref.path: {"$nin": [value, None]}}
        return {ref.path: {f"${op}": value}}
    if isinstance(pred, PIn):
        col = _column(pred.left)
        if col is None or not all(is_literal(i) for i in pred.items):
            return None
        ref = resolve(col)
        values = [literal_value(i, ref.type) for i in pred.items]
        has_null = any(v is None for v in values)
        values = [v for v in values if v is not None]
        if pred.neg:
            return {"$expr": False} if has_null else {ref.path: {"$nin": [*values, None]}}
        return {ref.path: {"$in": values}}
    if isinstance(pred, PLike):
        col = _column(pred.left)
        if col is None or not (isinstance(pred.pattern, exp.Literal) and pred.pattern.is_string):
            return None
        ref = resolve(col)
        regex = _like_regex(pred.pattern)
        flags = "si" if pred.ci else "s"
        if pred.neg:
            return {
                "$and": [{ref.path: {"$ne": None}}, {ref.path: {"$not": RegexValue(regex, flags)}}]
            }
        return {ref.path: {"$regex": regex, "$options": flags}}
    if isinstance(pred, PNull):
        col = _column(pred.left)
        if col is None:
            return None
        ref = resolve(col)
        return {ref.path: {"$ne": None}} if pred.neg else {ref.path: None}
    if isinstance(pred, PBool):
        col = _column(pred.expr)
        if col is None:
            return None
        return {resolve(col).path: pred.value}
    return None


def _strip(node: exp.Expr) -> exp.Expr:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _flip(op: str) -> str:
    return {"lt": "gt", "gt": "lt", "lte": "gte", "gte": "lte"}.get(op, op)


def conjuncts(node: exp.Expr) -> list[exp.Expr]:
    """Top-level AND terms of a SQL predicate."""
    if isinstance(node, exp.Paren):
        return conjuncts(node.this)
    if isinstance(node, exp.And):
        return [*conjuncts(node.this), *conjuncts(node.expression)]
    return [node]
