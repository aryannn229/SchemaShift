"""Result-set comparison: ordered with ORDER BY (tie groups may permute), else multiset."""

from __future__ import annotations

from typing import Any, Literal

from schemashift.models.base import FrozenModel
from schemashift.verification.normalize import normalize_row, rows_equal, sort_key


class RowDiff(FrozenModel):
    side: Literal["postgres", "mongo", "both"]
    index: int | None = None
    postgres: list[Any] | None = None
    mongo: list[Any] | None = None


class ResultDiff(FrozenModel):
    status: Literal["MATCH", "MISMATCH"]
    pg_rows: int
    mongo_rows: int
    differing: list[RowDiff] = []  # first 5
    hypothesis: str | None = None


def _multiset_diff(
    left: list[tuple[Any, ...]], right: list[tuple[Any, ...]]
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Rows only in ``left`` / only in ``right`` (tolerance-aware greedy matching)."""
    remaining = sorted(right, key=sort_key)
    only_left: list[tuple[Any, ...]] = []
    for row in sorted(left, key=sort_key):
        for i, cand in enumerate(remaining):
            if rows_equal(row, cand):
                del remaining[i]
                break
        else:
            only_left.append(row)
    return only_left, remaining


def _groups(
    rows: list[tuple[Any, ...]], positions: list[int]
) -> list[tuple[tuple[Any, ...], list[tuple[Any, ...]]]]:
    groups: list[tuple[tuple[Any, ...], list[tuple[Any, ...]]]] = []
    for row in rows:
        key = tuple(row[p] for p in positions)
        if groups and rows_equal(groups[-1][0], key):
            groups[-1][1].append(row)
        else:
            groups.append((key, [row]))
    return groups


def compare(
    pg_rows: list[tuple[Any, ...]],
    mongo_rows: list[tuple[Any, ...]],
    ordered: bool,
    order_positions: list[int] | None = None,
    global_aggregate: bool = False,
) -> ResultDiff:
    left = [normalize_row(r) for r in pg_rows]
    right = [normalize_row(r) for r in mongo_rows]
    base = {"pg_rows": len(left), "mongo_rows": len(right)}
    only_pg, only_mongo = _multiset_diff(left, right)
    if not only_pg and not only_mongo:
        if not ordered or _order_matches(left, right, order_positions):
            return ResultDiff(status="MATCH", **base)
        return ResultDiff(
            status="MISMATCH",
            differing=_first_order_difference(left, right),
            hypothesis="same rows, different order: NULL placement or the ORDER BY keys differ",
            **base,
        )
    diffs = [RowDiff(side="postgres", postgres=list(r)) for r in only_pg[:5]]
    diffs += [RowDiff(side="mongo", mongo=list(r)) for r in only_mongo[: max(0, 5 - len(diffs))]]
    return ResultDiff(
        status="MISMATCH",
        differing=diffs,
        hypothesis=_hypothesis(left, right, only_pg, only_mongo, global_aggregate),
        **base,
    )


def _order_matches(
    left: list[tuple[Any, ...]], right: list[tuple[Any, ...]], positions: list[int] | None
) -> bool:
    if not positions:
        return all(rows_equal(a, b) for a, b in zip(left, right, strict=True))
    lg, rg = _groups(left, positions), _groups(right, positions)
    if len(lg) != len(rg):
        return False
    for (lk, lrows), (rk, rrows) in zip(lg, rg, strict=True):
        if not rows_equal(lk, rk):
            return False
        a, b = _multiset_diff(lrows, rrows)
        if a or b:
            return False
    return True


def _first_order_difference(
    left: list[tuple[Any, ...]], right: list[tuple[Any, ...]]
) -> list[RowDiff]:
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        if not rows_equal(a, b):
            return [RowDiff(side="both", index=i, postgres=list(a), mongo=list(b))]
    return []


def _hypothesis(
    left: list[tuple[Any, ...]],
    right: list[tuple[Any, ...]],
    only_pg: list[tuple[Any, ...]],
    only_mongo: list[tuple[Any, ...]],
    global_aggregate: bool,
) -> str | None:
    if global_aggregate and len(left) == 1 and not right:
        return (
            "global aggregate over an empty input: PostgreSQL returns one row, "
            "MongoDB $group returns none"
        )
    if len(only_pg) == len(only_mongo) == 1 or (len(left) == len(right) and only_pg and only_mongo):
        for a, b in zip(only_pg, only_mongo, strict=False):
            for x, y in zip(a, b, strict=False):
                if x is None and y == 0:
                    return "SUM over an empty set: PostgreSQL returns NULL, MongoDB returns 0"
                if y is None and x == 0:
                    return "COUNT/SUM of no rows: PostgreSQL returns 0, MongoDB returns NULL"
                if isinstance(x, (int, float)) or hasattr(x, "is_finite"):
                    if isinstance(y, (int, float)) or hasattr(y, "is_finite"):
                        return (
                            "numeric precision: NUMERIC (exact) in PostgreSQL vs double in MongoDB"
                        )
    if len(left) != len(right):
        return "different row counts: a join or filter treats NULL / missing values differently"
    return None
