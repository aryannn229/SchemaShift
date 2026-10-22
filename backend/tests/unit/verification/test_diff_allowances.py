"""Documented allowances in result comparison: LIMIT ties, REAL precision, seed insertion order."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from schemashift.codegen.queries import TranslatedQuery
from schemashift.parser import parse
from schemashift.pipeline import compile_sql
from schemashift.verification.diff import compare
from schemashift.verification.normalize import TOLERANCE, rows_equal
from schemashift.verification.runner import REAL_TOLERANCE, _insertion_order, _tolerance_for

ROWS_PG = [("a", 5), ("b", 4), ("c", 3), ("d", 3)]


def test_ties_at_the_limit_cutoff_may_choose_different_members() -> None:
    mongo = [
        ("a", 5),
        ("b", 4),
        ("c", 3),
        ("e", 3),
    ]  # same keys, a different member of the last tie group
    plain = compare(ROWS_PG, mongo, True, [1])
    assert plain.status == "MISMATCH"
    allowed = compare(ROWS_PG, mongo, True, [1], limited=True)
    assert allowed.status == "MATCH" and "LIMIT cut-off" in (allowed.note or "")


def test_limit_allowance_does_not_hide_real_differences() -> None:
    # a different member in a group that is NOT the cut-off group is a real mismatch
    assert (
        compare(
            [("a", 5), ("b", 5), ("c", 3)], [("a", 5), ("x", 5), ("c", 3)], True, [1], limited=True
        ).status
        == "MISMATCH"
    )
    # different keys
    assert (
        compare(ROWS_PG, [("a", 5), ("b", 4), ("c", 3), ("d", 2)], True, [1], limited=True).status
        == "MISMATCH"
    )
    # a different count in the cut-off group
    assert (
        compare(ROWS_PG, [("a", 5), ("b", 4), ("c", 3)], True, [1], limited=True).status
        == "MISMATCH"
    )
    # unordered / no positions: no allowance
    assert (
        compare(ROWS_PG, [("a", 5), ("b", 4), ("c", 3), ("e", 3)], False, None, limited=True).status
        == "MISMATCH"
    )
    assert (
        compare(ROWS_PG, [("a", 5), ("b", 4), ("c", 3), ("e", 3)], True, None, limited=True).status
        == "MISMATCH"
    )
    assert compare([], [], True, [0], limited=True).status == "MATCH"


def test_a_looser_tolerance_for_real_columns() -> None:
    pg, mongo = [(Decimal("4926028.29006958"),)], [(Decimal("4926028.5"),)]  # ~4e-8 relative
    assert compare(pg, mongo, False).status == "MISMATCH"
    assert compare(pg, mongo, False, tolerance=REAL_TOLERANCE).status == "MATCH"
    assert rows_equal((1.0,), (1.0 + 5e-6,), REAL_TOLERANCE) and not rows_equal(
        (1.0,), (1.1,), REAL_TOLERANCE
    )
    assert TOLERANCE < REAL_TOLERANCE


SCHEMA = """
CREATE TABLE m (id INT PRIMARY KEY, price REAL, name TEXT);
CREATE TABLE d (id INT PRIMARY KEY, amount DOUBLE PRECISION);
"""


def _query(sql: str) -> Any:
    return parse(sql, "queries").all_queries()[0]


@pytest.mark.parametrize(
    ("sql", "loose"),
    [
        ("SELECT SUM(price) FROM m;", True),
        ("SELECT AVG(m.price * 2) AS x FROM m;", True),
        ("SELECT name FROM m;", False),  # a REAL column exists but is not referenced
        ("SELECT SUM(amount) FROM d;", False),  # DOUBLE PRECISION is 64-bit like MongoDB's double
        ("SELECT COUNT(*) FROM nowhere;", False),  # unknown table: no REAL column to find
    ],
)
def test_tolerance_is_loosened_only_when_a_real_column_is_referenced(sql: str, loose: bool) -> None:
    schema = compile_sql(SCHEMA).schema_
    tol, note = _tolerance_for(_query(sql), schema)
    assert (tol == REAL_TOLERANCE) is loose and (note is not None) is loose


def test_seed_insertion_order_puts_parents_first_whatever_the_declaration_order() -> None:
    # alphabetical declaration (as in Chinook): the child "album" comes before its parent "artist"
    sql = (
        "CREATE TABLE album (id INT PRIMARY KEY, artist_id INT NOT NULL);"
        "CREATE TABLE artist (id INT PRIMARY KEY);"
        "ALTER TABLE album ADD CONSTRAINT f FOREIGN KEY (artist_id) REFERENCES artist(id);"
    )
    schema = compile_sql(sql).schema_
    assert list(schema.tables) == ["album", "artist"]
    assert _insertion_order(schema) == ["artist", "album"]


def test_circular_not_null_keys_fall_back_to_declaration_order() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY, b_id INT NOT NULL);"
        "CREATE TABLE b (id INT PRIMARY KEY, a_id INT NOT NULL);"
        "ALTER TABLE a ADD FOREIGN KEY (b_id) REFERENCES b(id);"
        "ALTER TABLE b ADD FOREIGN KEY (a_id) REFERENCES a(id);"
    )
    assert _insertion_order(compile_sql(sql).schema_) == ["a", "b"]


def test_translated_query_records_limit() -> None:
    from tests.unit.codegen.helpers import translate

    schema = "CREATE TABLE t (id INT PRIMARY KEY, n INT);"
    limited, plain = translate(
        schema, "SELECT id FROM t ORDER BY n LIMIT 3; SELECT id FROM t ORDER BY n;", {}
    )
    assert isinstance(limited, TranslatedQuery) and limited.limited and not plain.limited
