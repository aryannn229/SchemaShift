"""SQL side of the SMT encoding: row-local constraints as Z3 formulas with three-valued logic.

A row is a set of variables: for every column a ``present`` flag (False = SQL NULL, stored as an
absent field)
and a typed value. A predicate is a pair ``(t, f)``: ``t`` = definitely TRUE, ``f`` = definitely
FALSE,
neither = UNKNOWN. A CHECK passes unless it is definitely FALSE (SQL's rule), a NOT NULL needs
``present``.

The supported fragment is documented in SPEC.md section 27; everything else raises ``Unsupported``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import z3
from sqlglot import exp

from schemashift.models.schema import Column, NormalizedType


class Unsupported(Exception):
    """A construct outside the supported fragment: the result is UNVERIFIED, never SAFE."""


INT_RANGES: dict[str, tuple[int, int]] = {
    "SMALLINT": (-(2**15), 2**15 - 1),
    "INTEGER": (-(2**31), 2**31 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
}


@dataclass(frozen=True)
class Var:
    column: str
    sql_type: NormalizedType
    present: Any  # z3 Bool
    value: Any  # z3 Int / Real / String / Bool
    enum_values: tuple[str, ...] = ()
    stored: Any = None  # NUMERIC(p,s): the value stored (rounded to the scale); CHECKs see it
    background: Any = None  # defines `stored` from `value` (linear constraints, always asserted)


@dataclass(frozen=True)
class NV:
    """A scalar: ``null`` (Bool) and ``val`` (Int, Real, String or Bool); ``kind`` names the
    sort."""

    null: Any
    val: Any
    kind: str  # "int" | "real" | "str" | "bool"
    bits: int = 0  # integer width (16, 32, 64): PostgreSQL integer arithmetic raises on overflow
    err: Any = None  # MongoDB: evaluating the expression raises (None = never)


@dataclass(frozen=True)
class TV:
    t: Any
    f: Any


def sort_kind(t: NormalizedType) -> str:
    if t.is_array:
        raise Unsupported("array column")
    if t.base in ("SMALLINT", "INTEGER", "BIGINT"):
        return "int"
    if t.base in ("DECIMAL", "DOUBLE"):
        return "real"
    if t.base == "FLOAT":
        raise Unsupported("single precision float (PostgreSQL real) differs from a BSON double")
    if t.base in ("TEXT", "VARCHAR", "CHAR", "ENUM"):
        return "str"
    if t.base == "BOOLEAN":
        return "bool"
    raise Unsupported(f"type {t.base}")


def _round_to_scale(x: Any, scale: int, name: str) -> tuple[Any, Any]:
    """PostgreSQL's numeric rounding (half away from zero) as linear constraints.

    ``k`` is the stored value scaled by 10^scale: for x >= 0, k - 1/2 <= x*m < k + 1/2; for x < 0,
    k - 1/2 < x*m <= k + 1/2. Returns (stored value, defining constraint).
    """
    m = 10**scale
    k = z3.Int(f"k_{name}")
    half = z3.RealVal("0.5")
    xm = x * m
    kr = z3.ToReal(k)
    defn = z3.If(
        x >= 0,
        z3.And(kr - half <= xm, xm < kr + half),
        z3.And(kr - half < xm, xm <= kr + half),
    )
    return kr / m, defn


def make_var(col: Column, enum_values: tuple[str, ...] = ()) -> Var:
    kind = sort_kind(col.sql_type)
    name = f"c_{col.name}"
    value = {
        "int": lambda: z3.Int(name),
        "real": lambda: z3.Real(name),
        "str": lambda: z3.String(name),
        "bool": lambda: z3.Bool(name),
    }[kind]()
    stored, background = None, None
    t = col.sql_type
    if t.base == "DECIMAL" and t.scale is not None:
        stored, background = _round_to_scale(value, t.scale, col.name)
    return Var(col.name, t, z3.Bool(f"p_{col.name}"), value, enum_values, stored, background)


def _as_real(n: NV) -> Any:
    return z3.ToReal(n.val) if n.kind == "int" else n.val


def _numeric(a: NV, b: NV) -> tuple[Any, Any, str]:
    if a.kind not in ("int", "real") or b.kind not in ("int", "real"):
        raise Unsupported("arithmetic on a non-number")
    if a.kind == "real" or b.kind == "real":
        return _as_real(a), _as_real(b), "real"
    return a.val, b.val, "int"


def _bits(t: NormalizedType) -> int:
    return {"SMALLINT": 16, "INTEGER": 32, "BIGINT": 64}.get(t.base, 0)


def _literal_bits(n: int) -> int:
    return 32 if -(2**31) <= n < 2**31 else 64


class SqlEncoder:
    def __init__(self, columns: dict[str, Var]) -> None:
        self.columns = columns
        self.guards: list[
            Any
        ] = []  # "no integer overflow" conditions of the expression being encoded

    def check_formula(self, node: exp.Expr) -> Any:
        """The row passes the CHECK: not definitely FALSE, and no arithmetic raised an error."""
        self.guards = []
        tv = self.pred(node)
        return z3.And(z3.Not(tv.f), *self.guards)

    def _int_result(self, a: NV, b: NV, val: Any) -> int:
        """Result width of integer arithmetic; records that an overflow raises (the row is
        rejected)."""
        bits = max(a.bits, b.bits) or 32
        lo, hi = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
        self.guards.append(z3.Implies(z3.Not(z3.Or(a.null, b.null)), z3.And(val >= lo, val <= hi)))
        return bits

    # ------------------------------------------------------------------ scalars
    def scalar(self, node: exp.Expr) -> NV:
        if isinstance(node, exp.Paren):
            return self.scalar(node.this)
        if isinstance(node, exp.Column):
            v = self.columns.get(str(node.name).lower()) or self.columns.get(str(node.name))
            if v is None:
                raise Unsupported(f"unknown column {node.name}")
            seen = v.stored if v.stored is not None else v.value
            return NV(z3.Not(v.present), seen, sort_kind(v.sql_type), _bits(v.sql_type))
        if isinstance(node, exp.Null):
            return NV(z3.BoolVal(True), z3.IntVal(0), "int")
        if isinstance(node, exp.Literal):
            if node.is_string:
                return NV(z3.BoolVal(False), z3.StringVal(str(node.this)), "str")
            text = str(node.this)
            if text.lstrip("-").isdigit():
                return NV(z3.BoolVal(False), z3.IntVal(int(text)), "int", _literal_bits(int(text)))
            return NV(z3.BoolVal(False), z3.RealVal(text), "real")
        if isinstance(node, exp.Neg):
            a = self.scalar(node.this)
            if a.kind not in ("int", "real"):
                raise Unsupported("negation of a non-number")
            bits = a.bits
            if a.kind == "int":
                bits = self._int_result(
                    a, NV(z3.BoolVal(False), z3.IntVal(0), "int", a.bits), -a.val
                )
            return NV(a.null, -a.val, a.kind, bits)
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul)):
            a, b = self.scalar(node.this), self.scalar(node.expression)
            x, y, kind = _numeric(a, b)
            val = (
                x + y
                if isinstance(node, exp.Add)
                else x - y
                if isinstance(node, exp.Sub)
                else x * y
            )
            bits = self._int_result(a, b, val) if kind == "int" else 0
            return NV(z3.Or(a.null, b.null), val, kind, bits)
        if isinstance(node, exp.Div):
            a, b = self.scalar(node.this), self.scalar(node.expression)
            if a.kind == "int" and b.kind == "int":
                raise Unsupported("integer division (truncation differs between the engines)")
            x, y, _ = _numeric(a, b)
            # division by zero is an error in PostgreSQL: outside the fragment
            return NV(z3.Or(a.null, b.null, y == 0), x / y, "real")
        if isinstance(node, (exp.Length,)):
            a = self.scalar(node.this)
            if a.kind != "str":
                raise Unsupported("LENGTH of a non-string")
            return NV(a.null, z3.Length(a.val), "int", 32)
        raise Unsupported(node.sql(dialect="postgres")[:60])

    # ------------------------------------------------------------------ predicates
    def pred(self, node: exp.Expr) -> TV:
        if isinstance(node, exp.Paren):
            return self.pred(node.this)
        if isinstance(node, exp.And):
            a, b = self.pred(node.this), self.pred(node.expression)
            return TV(z3.And(a.t, b.t), z3.Or(a.f, b.f))
        if isinstance(node, exp.Or):
            a, b = self.pred(node.this), self.pred(node.expression)
            return TV(z3.Or(a.t, b.t), z3.And(a.f, b.f))
        if isinstance(node, exp.Not):
            a = self.pred(node.this)
            return TV(a.f, a.t)
        if isinstance(node, exp.Boolean):
            return TV(z3.BoolVal(bool(node.this)), z3.BoolVal(not node.this))
        if isinstance(node, exp.Is):
            if isinstance(node.expression, exp.Null):
                n = self.scalar(node.this).null
                # sqlglot writes `x IS NOT NULL` as Is(..., negate=True)
                return TV(z3.Not(n), n) if node.args.get("negate") else TV(n, z3.Not(n))
            raise Unsupported("IS with a non-NULL operand")
        if isinstance(node, exp.Between):
            low = exp.GTE(this=node.this.copy(), expression=node.args["low"].copy())
            high = exp.LTE(this=node.this.copy(), expression=node.args["high"].copy())
            return self.pred(exp.And(this=low, expression=high))
        if isinstance(node, exp.In):
            if node.args.get("query") is not None or not node.expressions:
                raise Unsupported("IN with a subquery")
            eqs = [exp.EQ(this=node.this.copy(), expression=e.copy()) for e in node.expressions]
            out = self.pred(eqs[0])
            for e in eqs[1:]:
                nxt = self.pred(e)
                out = TV(z3.Or(out.t, nxt.t), z3.And(out.f, nxt.f))
            return out
        if isinstance(node, (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)):
            return self._compare(node)
        if isinstance(node, exp.Column):
            v = self.scalar(node)
            if v.kind != "bool":
                raise Unsupported("non-boolean column used as a condition")
            return TV(z3.And(z3.Not(v.null), v.val), z3.And(z3.Not(v.null), z3.Not(v.val)))
        raise Unsupported(node.sql(dialect="postgres")[:60])

    def _compare(self, node: exp.Binary) -> TV:
        a, b = self.scalar(node.this), self.scalar(node.expression)
        known = z3.And(z3.Not(a.null), z3.Not(b.null))
        if a.kind == "str" or b.kind == "str":
            if not (a.kind == b.kind == "str") or not isinstance(node, (exp.EQ, exp.NEQ)):
                raise Unsupported("string ordering (collation dependent) or mixed comparison")
            c = a.val == b.val if isinstance(node, exp.EQ) else a.val != b.val
        elif a.kind == "bool" or b.kind == "bool":
            if not (a.kind == b.kind == "bool") or not isinstance(node, (exp.EQ, exp.NEQ)):
                raise Unsupported("boolean comparison")
            c = a.val == b.val if isinstance(node, exp.EQ) else a.val != b.val
        else:
            x, y, _ = _numeric(a, b)
            ops: dict[type[exp.Binary], Any] = {
                exp.EQ: x == y,
                exp.NEQ: x != y,
                exp.LT: x < y,
                exp.LTE: x <= y,
                exp.GT: x > y,
                exp.GTE: x >= y,
            }
            c = ops[type(node)]
        return TV(z3.And(known, c), z3.And(known, z3.Not(c)))


# ---------------------------------------------------------------------- per-column constraints
def type_constraint(v: Var) -> Any:
    """What the column's SQL type accepts for a present value."""
    t = v.sql_type
    if t.base in INT_RANGES:
        lo, hi = INT_RANGES[t.base]
        return z3.And(v.value >= lo, v.value <= hi)
    if t.base == "DECIMAL" and t.precision is not None:
        # PostgreSQL rounds extra decimals to the scale and rejects only an overflow of the
        # precision
        scale = t.scale or 0
        seen = v.stored if v.stored is not None else v.value
        limit = z3.RealVal(10 ** (t.precision - scale))
        return z3.And(seen < limit, seen > -limit)
    if t.base in ("VARCHAR", "CHAR") and t.length:
        return z3.Length(v.value) <= t.length
    if t.base == "ENUM":
        if not v.enum_values:
            raise Unsupported("enum without values")
        return z3.Or(*[v.value == z3.StringVal(x) for x in v.enum_values])
    return z3.BoolVal(True)


def when_present(v: Var, formula: Any) -> Any:
    return z3.Implies(v.present, formula)
