"""MongoDB side of the SMT encoding: the generated ``$jsonSchema`` / ``$expr`` validator as Z3
formulas.

Only the fragment the code generator emits is understood. A document is modelled the way the
migration
writes it: a SQL NULL is an *absent* field (never an explicit null). Comparisons follow BSON order
for the
null/missing case (null sorts before every number), which is exactly the trap the generated
``$expr``
guards protect against.
"""

from __future__ import annotations

from typing import Any

import z3

from schemashift.equivalence.smt.sql import NV, Unsupported, Var, _as_real, sort_kind


def _e(n: NV) -> Any:
    return z3.BoolVal(False) if n.err is None else n.err


JSON_SCHEMA_KEYS = {
    "bsonType",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "enum",
    "maxLength",
    "minLength",
    "not",
}
BSON_INT = {"int": (-(2**31), 2**31 - 1), "long": (-(2**63), 2**63 - 1)}


class Clause:
    """One validator clause: the formula and where it came from (for the counterexample)."""

    def __init__(self, text: str, formula: Any, fields: tuple[str, ...] = ()) -> None:
        self.text, self.formula, self.fields = text, formula, fields


def _bson_formula(v: Var, bson_type: Any) -> Any:
    kinds = bson_type if isinstance(bson_type, list) else [bson_type]
    kind = sort_kind(v.sql_type)
    parts: list[Any] = []
    for k in kinds:
        if k in BSON_INT:
            if kind != "int":
                raise Unsupported(f"bsonType {k} on a non-integer column")
            lo, hi = BSON_INT[k]
            parts.append(z3.And(v.value >= lo, v.value <= hi))
        elif k in ("double", "decimal", "number"):
            if kind not in ("real", "int"):
                raise Unsupported(f"bsonType {k} on a non-numeric column")
            parts.append(z3.BoolVal(True))
        elif k == "string":
            if kind != "str":
                raise Unsupported("bsonType string on a non-text column")
            parts.append(z3.BoolVal(True))
        elif k == "bool":
            if kind != "bool":
                raise Unsupported("bsonType bool on a non-boolean column")
            parts.append(z3.BoolVal(True))
        else:
            raise Unsupported(f"bsonType {k}")
    return z3.Or(*parts) if len(parts) > 1 else parts[0]


def _const(v: Var, x: Any) -> Any:
    """A literal of the column's sort (enum members of any scalar kind)."""
    kind = sort_kind(v.sql_type)
    if kind == "str":
        return z3.StringVal(str(x))
    if kind == "bool":
        if not isinstance(x, bool):
            raise Unsupported("non-boolean enum member")
        return z3.BoolVal(x)
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise Unsupported("non-numeric enum member")
    if kind == "int":
        if isinstance(x, float) and x != int(x):
            raise Unsupported("fractional enum member on an integer column")
        return z3.IntVal(int(x))
    return z3.RealVal(str(x))


def _bound(v: Var, op: str, limit: Any, exclusive: bool) -> Any:
    val = z3.ToReal(v.value) if v.value.sort() == z3.IntSort() else v.value
    lim = z3.RealVal(str(limit))
    if op == "min":
        return val > lim if exclusive else val >= lim
    return val < lim if exclusive else val <= lim


def property_clauses(v: Var, field: str, schema: dict[str, Any]) -> list[Clause]:
    unknown = set(schema) - JSON_SCHEMA_KEYS
    if unknown:
        raise Unsupported(f"$jsonSchema keyword {sorted(unknown)[0]} on {field}")
    out: list[Clause] = []
    if "bsonType" in schema:
        out.append(
            Clause(
                f"{field} has bsonType {schema['bsonType']}",
                z3.Implies(v.present, _bson_formula(v, schema["bsonType"])),
                (field,),
            )
        )
    for key, op, excl_key in (
        ("minimum", "min", "exclusiveMinimum"),
        ("maximum", "max", "exclusiveMaximum"),
    ):
        if key in schema:
            exclusive = bool(schema.get(excl_key, False))
            if kind_is_text(v):
                raise Unsupported("numeric bound on a text column")
            out.append(
                Clause(
                    f"{field} {'>' if op == 'min' else '<'}"
                    f"{'' if exclusive else '='} {schema[key]}",
                    z3.Implies(v.present, _bound(v, op, schema[key], exclusive)),
                    (field,),
                )
            )
    if "not" in schema:
        if schema["not"] != {}:
            raise Unsupported("$jsonSchema not with a sub-schema")
        out.append(Clause(f"{field} can hold no value", z3.Not(v.present), (field,)))
    if "enum" in schema:
        out.append(
            Clause(
                f"{field} is one of {schema['enum']}",
                z3.Implies(v.present, z3.Or(*[v.value == _const(v, x) for x in schema["enum"]])),
                (field,),
            )
        )
    for key, cmp in (("maxLength", "<="), ("minLength", ">=")):
        if key in schema:
            length = z3.Length(v.value)
            f = length <= int(schema[key]) if cmp == "<=" else length >= int(schema[key])
            out.append(Clause(f"{field} {key} {schema[key]}", z3.Implies(v.present, f), (field,)))
    return out


def kind_is_text(v: Var) -> bool:
    return sort_kind(v.sql_type) == "str"


# ------------------------------------------------------------------------------ $expr
class MongoExpr:
    """Aggregation expressions over fields, with BSON null/missing ordering."""

    def __init__(self, fields: dict[str, Var]) -> None:
        self.fields = fields  # document field name -> variable

    def value(self, node: Any) -> NV:
        if node is None:
            return NV(z3.BoolVal(True), z3.IntVal(0), "int")
        if isinstance(node, bool):
            return NV(z3.BoolVal(False), z3.BoolVal(node), "bool")
        if isinstance(node, int):
            return NV(z3.BoolVal(False), z3.IntVal(node), "int")
        if isinstance(node, float):
            return NV(z3.BoolVal(False), z3.RealVal(str(node)), "real")
        if isinstance(node, str):
            if node.startswith("$$"):
                raise Unsupported(f"variable {node}")
            if node.startswith("$"):
                v = self.fields.get(node[1:])
                if v is None:
                    raise Unsupported(f"field {node}")
                return NV(z3.Not(v.present), v.value, sort_kind(v.sql_type))
            return NV(z3.BoolVal(False), z3.StringVal(node), "str")
        if isinstance(node, dict) and len(node) == 1:
            ((op, args),) = node.items()
            if op in ("$add", "$subtract", "$multiply"):
                a, b = (self.value(x) for x in args)
                if a.kind not in ("int", "real") or b.kind not in ("int", "real"):
                    raise Unsupported(f"{op} on a non-number")
                real = "real" in (a.kind, b.kind)
                x = _as_real(a) if real else a.val
                y = _as_real(b) if real else b.val
                val = x + y if op == "$add" else x - y if op == "$subtract" else x * y
                return NV(
                    z3.Or(a.null, b.null), val, "real" if real else "int", err=z3.Or(_e(a), _e(b))
                )
            if op == "$strLenCP":
                x = self.value(args)
                if x.kind != "str":
                    raise Unsupported("$strLenCP of a non-string")
                # raises on null/missing: the expression errors and the document is rejected
                return NV(z3.BoolVal(False), z3.Length(x.val), "int", err=z3.Or(_e(x), x.null))
            if op == "$literal":
                return self.value(args)
        raise Unsupported(f"expression {str(node)[:60]}")

    def truth(self, node: Any) -> Any:
        """The expression evaluates to true without raising."""
        t, e = self.cond(node)
        return z3.And(z3.Not(e), t)

    def cond(self, node: Any) -> tuple[Any, Any]:
        """(true, raises): `$or`/`$and` stop at the first deciding argument, like MongoDB."""
        if isinstance(node, bool):
            return z3.BoolVal(node), z3.BoolVal(False)
        if isinstance(node, dict) and len(node) == 1:
            ((op, args),) = node.items()
            if op in ("$and", "$or"):
                t, e = self.cond(args[0])
                for item in args[1:]:
                    t2, e2 = self.cond(item)
                    if op == "$or":
                        e = z3.Or(e, z3.And(z3.Not(t), e2))
                        t = z3.Or(t, t2)
                    else:
                        e = z3.Or(e, z3.And(t, e2))
                        t = z3.And(t, t2)
                return t, e
            if op == "$not":
                t, e = self.cond(args[0] if isinstance(args, list) else args)
                return z3.Not(t), e
            if op in ("$eq", "$ne", "$gt", "$gte", "$lt", "$lte"):
                a, b = self.value(args[0]), self.value(args[1])
                return self.compare(op, a, b), z3.Or(_e(a), _e(b))
            if op == "$in":
                needle, haystack = args
                if not isinstance(haystack, list):
                    raise Unsupported("$in over a non-literal array")
                a = self.value(needle)
                hs = [self.value(h) for h in haystack]
                return (
                    z3.Or(*[self.compare("$eq", a, h) for h in hs]),
                    z3.Or(_e(a), *[_e(h) for h in hs]),
                )
        raise Unsupported(f"condition {str(node)[:60]}")

    @staticmethod
    def compare(op: str, a: NV, b: NV) -> Any:
        both_null = z3.And(a.null, b.null)
        if a.kind == "str" or b.kind == "str":
            if (
                not (a.kind == b.kind == "str")
                and not (a.kind == "int" and a.null)
                and not (b.kind == "int" and b.null)
            ):
                raise Unsupported("mixed-type comparison")
            if op not in ("$eq", "$ne"):
                if not (z3.is_true(a.null) or z3.is_true(b.null)):
                    raise Unsupported("string ordering (collation dependent)")
                # against a literal null only the null/missing ordering matters
                lt_n = z3.And(a.null, z3.Not(b.null))
                gt_n = z3.And(z3.Not(a.null), b.null)
                return {
                    "$lt": lt_n,
                    "$lte": z3.Or(lt_n, both_null),
                    "$gt": gt_n,
                    "$gte": z3.Or(gt_n, both_null),
                }[op]
            same = z3.Or(both_null, z3.And(z3.Not(a.null), z3.Not(b.null), a.val == b.val))
            return same if op == "$eq" else z3.Not(same)
        if a.kind == "bool" or b.kind == "bool":
            raise Unsupported("boolean comparison")
        x = _as_real(a) if "real" in (a.kind, b.kind) else a.val
        y = _as_real(b) if "real" in (a.kind, b.kind) else b.val
        neither = z3.And(z3.Not(a.null), z3.Not(b.null))
        # null/missing sorts before every number and equals itself
        lt = z3.Or(z3.And(a.null, z3.Not(b.null)), z3.And(neither, x < y))
        eq = z3.Or(both_null, z3.And(neither, x == y))
        gt = z3.Or(z3.And(z3.Not(a.null), b.null), z3.And(neither, x > y))
        return {
            "$eq": eq,
            "$ne": z3.Not(eq),
            "$lt": lt,
            "$lte": z3.Or(lt, eq),
            "$gt": gt,
            "$gte": z3.Or(gt, eq),
        }[op]
