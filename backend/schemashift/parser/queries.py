"""Query handling: validate the supported SELECT/INSERT/UPDATE/DELETE subset."""

from __future__ import annotations

from sqlglot import exp

from schemashift.models.query import Query, QueryKind
from schemashift.models.source import SourceSpan
from schemashift.parser import errors
from schemashift.parser.errors import ParseError, unsupported
from schemashift.parser.splitter import RawStatement, SourceMap
from schemashift.parser.statement import parse_sql

_ALLOWED_FUNCS = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Cast)


def _is_equality_conjunction(node: exp.Expr) -> bool:
    if isinstance(node, exp.EQ):
        return True
    if isinstance(node, exp.And):
        return _is_equality_conjunction(node.this) and _is_equality_conjunction(node.expression)
    if isinstance(node, exp.Paren):
        return _is_equality_conjunction(node.this)
    return False


def _reject_common(root: exp.Expr, span: SourceSpan) -> None:
    if root.find(exp.Lateral) is not None:
        raise unsupported("LATERAL", "LATERAL is not supported", span)
    if any(n is not root for n in root.find_all(exp.Select)):
        raise unsupported(
            "SUBQUERY", "subqueries (correlated or not) are not supported in v1", span
        )
    if root.find(exp.With) is not None:
        raise unsupported("CTE", "common table expressions (WITH) are not supported", span)
    if root.find(exp.Window) is not None:
        raise unsupported("WINDOW_FUNCTION", "window functions (OVER) are not supported", span)


def validate_select(ast: exp.Expr, span: SourceSpan) -> None:
    if isinstance(ast, (exp.Union, exp.Intersect, exp.Except)):
        raise unsupported("SET_OPERATION", "UNION/INTERSECT/EXCEPT are not supported", span)
    if not isinstance(ast, exp.Select):
        raise ParseError(errors.SYNTAX_ERROR, "Syntax error: expected SELECT", span)
    _reject_common(ast, span)
    for join in ast.args.get("joins") or []:
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        if side in ("RIGHT", "FULL"):
            raise unsupported("OUTER_JOIN", f"{side} JOIN is not supported", span)
        if kind in ("CROSS", "OUTER") or join.args.get("method") or join.args.get("using"):
            raise unsupported("JOIN", "only INNER and LEFT joins with ON equality", span)
        on = join.args.get("on")
        if on is None or not _is_equality_conjunction(on):
            raise unsupported("JOIN", "join conditions must be equality (ON a = b [AND ...])", span)
    for fn in ast.find_all(exp.Func):
        if not isinstance(fn, (*_ALLOWED_FUNCS, exp.Connector, exp.Binary, exp.Unary)):
            raise unsupported(
                "FUNCTION_CALL",
                f"function {fn.sql_name()} is not supported (only COUNT/SUM/AVG/MIN/MAX)",
                span,
            )
    distinct = ast.args.get("distinct")
    if isinstance(distinct, exp.Distinct) and distinct.args.get("on"):
        raise unsupported("QUERY_FEATURE", "DISTINCT ON is not supported", span)
    if ast.args.get("locks"):
        raise unsupported("QUERY_FEATURE", "row locking clauses are not supported", span)


def validate_dml(ast: exp.Expr, span: SourceSpan) -> None:
    if isinstance(ast, exp.Insert) and not isinstance(ast.args.get("expression"), exp.Values):
        raise unsupported("QUERY_FEATURE", "only INSERT ... VALUES is supported", span)
    _reject_common(ast, span)
    if ast.args.get("returning"):
        raise unsupported("QUERY_FEATURE", "RETURNING is not supported", span)
    if isinstance(ast, exp.Insert):
        if ast.args.get("conflict"):
            raise unsupported("QUERY_FEATURE", "ON CONFLICT is not supported", span)
    elif isinstance(ast, exp.Update):
        if ast.args.get("from_") or ast.args.get("from"):
            raise unsupported("QUERY_FEATURE", "UPDATE ... FROM is not supported", span)
    elif isinstance(ast, exp.Delete) and ast.args.get("using"):
        raise unsupported("QUERY_FEATURE", "DELETE ... USING is not supported", span)


_KIND_BY_CLASS: dict[str, QueryKind] = {
    "select": "SELECT",
    "insert": "INSERT",
    "update": "UPDATE",
    "delete": "DELETE",
}


def build_query(qid: str, cls: str, raw: RawStatement, smap: SourceMap, span: SourceSpan) -> Query:
    ast = parse_sql(raw.text, raw, smap, span)
    if cls == "select":
        validate_select(ast, span)
    else:
        validate_dml(ast, span)
    return Query(id=qid, kind=_KIND_BY_CLASS[cls], ast=ast, raw_sql=raw.text, span=span)
