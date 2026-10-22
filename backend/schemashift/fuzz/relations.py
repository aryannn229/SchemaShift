"""Metamorphic relations: properties that must hold whatever the "right" answer is.

Each relation runs several related queries on the MongoDB side (and the PostgreSQL side as a
control) and checks how the results relate. They catch bugs that differential testing can hide
(for instance when the normalizer or a shared mistake makes both sides agree wrongly).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from schemashift.fuzz.harness import FuzzSession, SelectOutcome
from schemashift.fuzz.spec import (
    Pred,
    SelectSpec,
    p_and,
    p_not,
    p_unknown,
    render_select,
)
from schemashift.models.base import FrozenModel
from schemashift.verification.normalize import normalize_row, sort_key

Result = tuple[bool, list["Finding"]]  # (was the relation actually checked?, violations)


class Finding(FrozenModel):
    """A differential mismatch or a violated metamorphic relation."""

    kind: str  # "differential" | "tlp" | "tautology" | "commute" | "count" | "plan"
    summary: str
    sqls: tuple[str, ...] = ()
    detail: dict[str, Any] = {}


def multiset(rows: list[tuple[Any, ...]]) -> Counter[str]:
    return Counter(sort_key(normalize_row(r)) for r in rows)


def _conj(spec: SelectSpec, extra: Pred) -> SelectSpec:
    base = spec.where
    if base is None or base.op == "true":
        return spec.with_where(extra)
    return spec.with_where(p_and(base, extra))


def _plain(spec: SelectSpec) -> SelectSpec:
    """The same FROM/WHERE as a plain row query (no limit/offset/order) for relation checks."""
    return spec.model_copy(update={"limit": None, "offset": None, "order_by": ()})


def _run(session: FuzzSession, spec: SelectSpec) -> SelectOutcome:
    return session.select(render_select(spec))


def tlp(session: FuzzSession, spec: SelectSpec, p: Pred) -> Result:
    """Ternary logic partitioning: rows(p) + rows(NOT p) + rows(p IS UNKNOWN) == rows(all)."""
    if spec.distinct:
        return False, []  # DISTINCT results of the partitions do not add up (true in SQL too)
    base = _plain(spec)
    parts = [
        _run(session, base),
        _run(session, _conj(base, p)),
        _run(session, _conj(base, p_not(p))),
        _run(session, _conj(base, p_unknown(p))),
    ]
    if not all(o.comparable for o in parts):
        return False, []  # an untranslatable fragment: reported as "skipped", not a violation
    whole, a, b, c = (multiset(o.mongo) for o in parts)
    if a + b + c != whole:
        return True, [
            Finding(
                kind="tlp",
                summary="rows(p) + rows(NOT p) + rows(p IS NULL) != rows(all) on MongoDB",
                sqls=tuple(o.sql for o in parts),
                detail={
                    "mongo_counts": [len(o.mongo) for o in parts],
                    "pg_counts": [len(o.pg) for o in parts],
                },
            )
        ]
    return True, []


def tautology(session: FuzzSession, spec: SelectSpec, column: str) -> Result:
    """AND-ing a tautology on a nullable column must not change the result."""
    base = _plain(spec)
    taut = Pred(
        op="or",
        items=(Pred(op="null", column=column), Pred(op="null", column=column, negated=True)),
    )
    left, right = _run(session, base), _run(session, _conj(base, taut))
    if not (left.comparable and right.comparable):
        return False, []
    if multiset(left.mongo) != multiset(right.mongo):
        return True, [
            Finding(
                kind="tautology",
                summary="adding (c IS NULL OR c IS NOT NULL) changed the MongoDB result",
                sqls=(left.sql, right.sql),
                detail={"mongo_counts": [len(left.mongo), len(right.mongo)]},
            )
        ]
    return True, []


def commute(session: FuzzSession, spec: SelectSpec) -> Result:
    """Reordering the terms of a top-level AND/OR must not change the result."""
    w = spec.where
    if w is None or w.op not in ("and", "or") or len(w.items) < 2:
        return False, []
    flipped = w.model_copy(update={"items": tuple(reversed(w.items))})
    base = _plain(spec)
    left, right = _run(session, base), _run(session, base.with_where(flipped))
    if not (left.comparable and right.comparable):
        return False, []
    if multiset(left.mongo) != multiset(right.mongo):
        return True, [
            Finding(
                kind="commute",
                summary="reordering AND/OR terms changed the MongoDB result",
                sqls=(left.sql, right.sql),
                detail={"mongo_counts": [len(left.mongo), len(right.mongo)]},
            )
        ]
    return True, []


def count_matches_rows(session: FuzzSession, spec: SelectSpec) -> Result:
    """COUNT(*) over the same FROM/WHERE equals the number of rows of the plain query."""
    if spec.is_aggregate or spec.distinct:
        return False, []
    base = _plain(spec)
    rows = _run(session, base)
    count = _run(session, base.model_copy(update={"columns": (), "expressions": (), "aggs": ()}))
    if not (rows.comparable and count.comparable and count.mongo):
        return False, []
    if int(count.mongo[0][0]) != len(rows.mongo):
        return True, [
            Finding(
                kind="count",
                summary="COUNT(*) differs from the number of rows returned on MongoDB",
                sqls=(rows.sql, count.sql),
                detail={"count": int(count.mongo[0][0]), "rows": len(rows.mongo)},
            )
        ]
    return True, []


Relation = Callable[..., Result]
