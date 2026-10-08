"""Evaluate a SQL expression against a row with three-valued logic (None = SQL NULL)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlglot import exp

from schemashift.codegen.predicate import like_to_regex


class Unevaluable(Exception):
    """The expression uses something this evaluator does not support."""


def _num(a: Any, b: Any) -> tuple[Any, Any]:
    if isinstance(a, Decimal) and isinstance(b, float):
        return a, Decimal(str(b))
    if isinstance(a, float) and isinstance(b, Decimal):
        return Decimal(str(a)), b
    return a, b


def evaluate(node: exp.Expr, row: Mapping[str, Any]) -> Any:
    if isinstance(node, exp.Paren):
        return evaluate(node.this, row)
    if isinstance(node, exp.Column):
        return row.get(str(node.name) if node.this.args.get("quoted") else str(node.name).lower())
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        text = str(node.this)
        return int(text) if re.fullmatch(r"-?\d+", text) else Decimal(text)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Neg):
        v = evaluate(node.this, row)
        return None if v is None else -v
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        a, b = evaluate(node.this, row), evaluate(node.expression, row)
        if a is None or b is None:
            return None
        a, b = _num(a, b)
        if isinstance(node, exp.Add):
            return a + b
        if isinstance(node, exp.Sub):
            return a - b
        if isinstance(node, exp.Mul):
            return a * b
        return None if b == 0 else a / b
    if isinstance(node, exp.And):
        a, b = evaluate(node.this, row), evaluate(node.expression, row)
        if a is False or b is False:
            return False
        return None if a is None or b is None else True
    if isinstance(node, exp.Or):
        a, b = evaluate(node.this, row), evaluate(node.expression, row)
        if a is True or b is True:
            return True
        return None if a is None or b is None else False
    if isinstance(node, exp.Not):
        v = evaluate(node.this, row)
        return None if v is None else (not v)
    ops = {exp.EQ: "==", exp.NEQ: "!=", exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">="}
    for cls, op in ops.items():
        if type(node) is cls:
            a, b = evaluate(node.this, row), evaluate(node.expression, row)
            if a is None or b is None:
                return None
            a, b = _num(a, b)
            try:
                return {
                    "==": a == b,
                    "!=": a != b,
                    "<": a < b,
                    "<=": a <= b,
                    ">": a > b,
                    ">=": a >= b,
                }[op]
            except TypeError as exc:
                raise Unevaluable(str(exc)) from exc
    if isinstance(node, exp.Between):
        v = evaluate(node.this, row)
        lo, hi = evaluate(node.args["low"], row), evaluate(node.args["high"], row)
        if v is None or lo is None or hi is None:
            return None
        inside = lo <= v <= hi
        return (not inside) if node.args.get("negate") else inside
    if isinstance(node, exp.In):
        if node.args.get("query") is not None:
            raise Unevaluable("IN (subquery)")
        v = evaluate(node.this, row)
        items = [evaluate(i, row) for i in node.expressions]
        if v is None:
            return None
        found = any(v == _num(v, i)[1] for i in items if i is not None)
        negate = bool(node.args.get("negate"))
        if found:
            return not negate
        return None if any(i is None for i in items) else negate
    if isinstance(node, exp.Is):
        v = evaluate(node.this, row)
        if isinstance(node.expression, exp.Null):
            return (v is not None) if node.args.get("negate") else (v is None)
        raise Unevaluable("IS TRUE/FALSE")
    if isinstance(node, (exp.Like, exp.ILike)):
        v, pattern = evaluate(node.this, row), evaluate(node.expression, row)
        if v is None or pattern is None:
            return None
        flags = re.DOTALL | (re.IGNORECASE if isinstance(node, exp.ILike) else 0)
        matched = re.fullmatch(like_to_regex(str(pattern))[1:-1], str(v), flags) is not None
        return (not matched) if node.args.get("negate") else matched
    if isinstance(node, exp.Length):
        v = evaluate(node.this, row)
        return None if v is None else len(str(v))
    if isinstance(node, exp.Upper):
        v = evaluate(node.this, row)
        return None if v is None else str(v).upper()
    if isinstance(node, exp.Lower):
        v = evaluate(node.this, row)
        return None if v is None else str(v).lower()
    if isinstance(node, exp.Abs):
        v = evaluate(node.this, row)
        return None if v is None else abs(v)
    if isinstance(node, exp.Coalesce):
        for arg in [node.this, *node.expressions]:
            v = evaluate(arg, row)
            if v is not None:
                return v
        return None
    raise Unevaluable(node.sql(dialect="postgres"))


def passes_check(node: exp.Expr, row: Mapping[str, Any]) -> bool:
    """CHECK semantics: pass unless the predicate is definitely FALSE. Unknown syntax passes."""
    try:
        return evaluate(node, row) is not False
    except (Unevaluable, TypeError, ArithmeticError):
        return True
