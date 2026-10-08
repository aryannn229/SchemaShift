"""Which CHECK / partial-index predicates can be enforced by a MongoDB validator."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div)
_LOGICAL = (exp.And, exp.Or, exp.Not, exp.Paren)


@dataclass(frozen=True)
class PredicateAnalysis:
    translatable: bool
    fragments: tuple[str, ...]  # SQL of the parts that cannot be translated
    columns: tuple[str, ...]
    needs_expr: bool  # cross-column or arithmetic: needs $expr, not just $jsonSchema


def _columns(node: exp.Expr) -> list[str]:
    return list(dict.fromkeys(str(c.name) for c in node.find_all(exp.Column)))


def analyze_predicate(expr: exp.Expr) -> PredicateAnalysis:
    """Walk the predicate; collect untranslatable fragments (outermost first)."""
    fragments: list[str] = []
    needs_expr = False

    def visit(node: exp.Expr) -> None:
        nonlocal needs_expr
        if isinstance(node, _LOGICAL):
            for child in node.args.values():
                _visit_arg(child)
        elif isinstance(node, _COMPARISONS):
            if len(_columns(node)) > 1:
                needs_expr = True
            for side in (node.this, node.expression):
                value(side)
        elif isinstance(node, exp.In):
            if node.args.get("query") is not None:
                fragments.append(node.sql(dialect="postgres"))
                return
            value(node.this)
            for item in node.expressions:
                value(item)
        elif isinstance(node, exp.Between):
            if len(_columns(node)) > 1:
                needs_expr = True
            for part in (node.this, node.args.get("low"), node.args.get("high")):
                if part is not None:
                    value(part)
        elif isinstance(node, exp.Like):
            pattern = node.expression
            if isinstance(node.this, exp.Column) and (
                isinstance(pattern, exp.Literal) and pattern.is_string
            ):
                return
            fragments.append(node.sql(dialect="postgres"))
        elif isinstance(node, exp.Is):
            value(node.this)
            value(node.expression)
        else:
            fragments.append(node.sql(dialect="postgres"))

    def _visit_arg(child: object) -> None:
        if isinstance(child, exp.Expr):
            visit(child)

    def value(node: exp.Expr | None) -> None:
        nonlocal needs_expr
        if node is None or isinstance(node, (exp.Column, exp.Literal, exp.Null, exp.Boolean)):
            return
        if isinstance(node, exp.Paren):
            value(node.this)
        elif isinstance(node, exp.Neg):
            value(node.this)
        elif isinstance(node, exp.Length):
            value(node.this)
        elif isinstance(node, _ARITHMETIC):
            needs_expr = True
            value(node.this)
            value(node.expression)
        else:
            fragments.append(node.sql(dialect="postgres"))

    visit(expr)
    return PredicateAnalysis(
        translatable=not fragments,
        fragments=tuple(dict.fromkeys(fragments)),
        columns=tuple(_columns(expr)),
        needs_expr=needs_expr,
    )
