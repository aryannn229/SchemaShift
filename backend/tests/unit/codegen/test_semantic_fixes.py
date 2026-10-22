"""Unit tests for the SQL-vs-MongoDB semantic fixes found by differential and metamorphic fuzzing.

Each test pins the shape of the generated pipeline; tests/regressions/ holds the minimal
reproducers that prove the behaviour against real databases.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlglot

from schemashift.codegen import generate_code
from schemashift.codegen.collections import EN_COLLATION, build_collections
from schemashift.codegen.layout import build_layouts
from schemashift.codegen.predicate import (
    ColRef,
    PUnknown,
    Untranslatable,
    parse_predicate,
    to_expr,
    to_query,
)
from schemashift.models import NormalizedType
from schemashift.pipeline import CompileOptions, compile_sql
from tests.unit.codegen.helpers import translate

SCHEMA = """
CREATE TYPE mood AS ENUM ('draft', 'open', 'closed');
CREATE TABLE p (id INT PRIMARY KEY, name TEXT, n INT, big BIGINT, amount NUMERIC(10,2));
CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id), state mood, other mood, tag TEXT);
CREATE TABLE g (id INT PRIMARY KEY, c_id INT NOT NULL REFERENCES c(id));
CREATE TABLE h (id INT PRIMARY KEY, c_id INT REFERENCES c(id));
"""
EMBED_C: dict[str, Any] = {"fk:c.p_id->p.id": "EMBED"}


def pipeline(sql: str, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = translate(SCHEMA, sql, plan)
    assert out[0].error is None, out[0].error
    assert out[0].pipeline is not None
    return out[0].pipeline


def names(p: list[dict[str, Any]]) -> list[str]:
    return [next(iter(s)) for s in p]


# ---------------------------------------------------------------------------- LIMIT / OFFSET
def test_limit_zero_becomes_an_impossible_match() -> None:
    p = pipeline("SELECT id FROM p ORDER BY id LIMIT 0;")
    assert {"$match": {"$expr": False}} in p
    assert "$limit" not in names(p)


def test_positive_limit_is_kept() -> None:
    assert {"$limit": 3} in pipeline("SELECT id FROM p ORDER BY id LIMIT 3 OFFSET 2;")


# ------------------------------------------------------------------- global aggregate rows
def test_global_aggregate_over_empty_input_returns_one_row() -> None:
    p = pipeline("SELECT COUNT(*) AS c, SUM(n) AS s, AVG(n) AS a, MIN(n) AS lo FROM p;")
    facet = next(s for s in p if "$facet" in s)["$facet"]["g"][0]["$group"]
    assert facet["_id"] is None
    root = next(s for s in p if "$ifNull" in str(s.get("$replaceRoot")))["$replaceRoot"]
    default = root["newRoot"]["$ifNull"][1]["$literal"]
    assert default["_id"] is None
    by_op = {k: (v if not isinstance(v, dict) else None) for k, v in default.items()}
    # COUNT and the SUM bookkeeping default to 0, AVG / MIN default to NULL
    assert by_op["__a0"] == 0 and by_op["__a1"] == 0 and by_op["__a1_n"] == 0
    assert by_op["__a2"] is None and by_op["__a3"] is None


def test_grouped_aggregate_has_no_facet() -> None:
    p = pipeline("SELECT n, COUNT(*) FROM p GROUP BY n;")
    assert "$facet" not in names(p) and "$group" in names(p)


def test_count_distinct_default_is_an_empty_set() -> None:
    p = pipeline("SELECT COUNT(DISTINCT n) AS c FROM p;")
    root = next(s for s in p if "$ifNull" in str(s.get("$replaceRoot")))["$replaceRoot"]
    assert root["newRoot"]["$ifNull"][1]["$literal"]["__a0"] == []


# ------------------------------------------------------------------------------ ORDER BY
def test_repeated_order_key_keeps_the_first_direction() -> None:
    p = pipeline("SELECT id FROM p ORDER BY id DESC, id ASC, name ASC;")
    sort = next(s for s in p if "$sort" in s)["$sort"]
    assert sort["p._id"] == -1
    assert list(sort).count("p._id") == 1


def test_null_flags_are_computed_after_the_keys_they_read() -> None:
    p = pipeline("SELECT id FROM c ORDER BY state ASC;")
    adds = [s["$addFields"] for s in p if "$addFields" in s]
    key_stage = next(a for a in adds if "_k0" in a)
    flag_stage = next(a for a in adds if "_n0" in a)
    assert "_n0" not in key_stage and "_k0" not in flag_stage
    assert p.index({"$addFields": key_stage}) < p.index({"$addFields": flag_stage})


def test_enum_order_by_uses_declaration_order() -> None:
    p = pipeline("SELECT id FROM c ORDER BY state;")
    key = next(s["$addFields"]["_k0"] for s in p if "$addFields" in s and "_k0" in s["$addFields"])
    assert key["$cond"][1] == {"$indexOfArray": [["draft", "open", "closed"], "$c._id"]} or (
        key["$cond"][1]["$indexOfArray"][0] == ["draft", "open", "closed"]
    )
    assert key["$cond"][2] is None  # NULL stays NULL so NULLS LAST still applies


def test_enum_output_column_sorts_by_declaration_after_grouping() -> None:
    p = pipeline("SELECT state, COUNT(*) AS n FROM c GROUP BY state ORDER BY state;")
    adds = [s["$addFields"] for s in p if "$addFields" in s]
    assert any("_k0" in a and "$indexOfArray" in str(a["_k0"]) for a in adds)


# ----------------------------------------------------------------------------- enum predicates
ENUM_TYPE = NormalizedType(base="ENUM", enum_name="mood")
VALUES = ("draft", "open", "closed")


def enum_resolve(col: Any) -> ColRef:
    return ColRef(path=str(col.name), type=ENUM_TYPE, enum_values=VALUES)


def parse(sql: str) -> Any:
    return parse_predicate(sqlglot.parse_one(sql, dialect="postgres"))


@pytest.mark.parametrize(
    ("sql", "allowed"),
    [
        ("state < 'open'", ["draft"]),
        ("state <= 'open'", ["draft", "open"]),
        ("state > 'open'", ["closed"]),
        ("state >= 'open'", ["open", "closed"]),
        ("state NOT BETWEEN 'draft' AND 'open'", None),
    ],
)
def test_enum_ordering_against_a_literal_becomes_membership(sql: str, allowed: Any) -> None:
    q = to_query(parse(sql), enum_resolve)
    if allowed is None:  # a negated BETWEEN is an OR of two range tests
        assert "$or" in q
    else:
        assert q == {"state": {"$in": allowed}}


def test_enum_equality_stays_plain() -> None:
    assert to_query(parse("state = 'open'"), enum_resolve) == {"state": "open"}


def test_enum_column_to_column_comparison_uses_positions() -> None:
    expr = to_expr(parse("state < other"), enum_resolve, "where")
    text = str(expr)
    assert "$indexOfArray" in text and "$lt" in text


def test_unknown_enum_literal_is_not_translated() -> None:
    with pytest.raises(Untranslatable):
        to_expr(parse("state < 'nope'"), enum_resolve, "where")


def test_min_max_over_an_enum_is_reported_untranslatable() -> None:
    out = translate(SCHEMA, "SELECT MAX(state) FROM c;", {})
    assert out[0].error and "enum" in out[0].error


# ------------------------------------------------------------- IS NULL over a boolean expression
def test_is_null_over_a_predicate_is_the_unknown_truth_value() -> None:
    p = parse("(n > 5) IS NULL")
    assert isinstance(p, PUnknown) and not p.neg
    expr = to_expr(
        p, lambda c: ColRef(path=str(c.name), type=NormalizedType(base="INTEGER")), "where"
    )
    assert "$not" in expr and "$or" in expr["$not"][0]


def test_is_not_null_over_a_predicate_is_decided() -> None:
    p = parse("(n > 5) IS NOT NULL")
    assert isinstance(p, PUnknown) and p.neg
    expr = to_expr(
        p, lambda c: ColRef(path=str(c.name), type=NormalizedType(base="INTEGER")), "where"
    )
    assert "$or" in expr and "$not" not in expr


def test_is_null_over_a_column_is_unchanged() -> None:
    assert not isinstance(parse("(n) IS NULL"), PUnknown)


def test_unknown_predicate_falls_back_to_expr_in_a_match() -> None:
    p = pipeline("SELECT id FROM p WHERE (n > 5) IS NULL;")
    assert "$expr" in next(s for s in p if "$match" in s)["$match"]


# -------------------------------------------------------------------------- integer division
def test_integer_division_truncates() -> None:
    p = pipeline("SELECT id / 2 AS h, n / 3 AS t FROM p;")
    proj = next(s for s in p if "$project" in s)["$project"]
    assert "$trunc" in str(proj["h"]) and "$trunc" in str(proj["t"])


@pytest.mark.parametrize("expr", ["amount / 2", "id / 2.0", "id / amount", "(id + 1) / amount"])
def test_non_integer_division_is_exact(expr: str) -> None:
    p = pipeline(f"SELECT {expr} AS x FROM p;")
    assert "$trunc" not in str(next(s for s in p if "$project" in s))


def test_nested_integer_division() -> None:
    p = pipeline("SELECT (id + 1) / -2 AS x FROM p;")
    assert "$trunc" in str(next(s for s in p if "$project" in s))


# ----------------------------------------------------------------------- LEFT JOIN null safety
def test_inner_join_on_the_host_of_a_left_joined_child_requires_the_child() -> None:
    """t JOIN host: rows where the LEFT-joined embedded child is absent must drop out."""
    p = pipeline(
        "SELECT g.id, c.id, p.id FROM g LEFT JOIN c ON g.c_id = c.id JOIN p ON c.p_id = p.id;",
        EMBED_C,
    )
    assert any("$match" in s and "$expr" in s["$match"] for s in p)


def test_left_joined_dropped_key_is_null_when_the_child_is_absent() -> None:
    p = pipeline("SELECT p.id, c.p_id FROM p LEFT JOIN c ON p.id = c.p_id;", EMBED_C)
    proj = next(s for s in p if "$project" in s)["$project"]
    assert "$cond" in str(proj["p_id"])  # guarded by the presence of the embedded element


def test_colref_present_guard() -> None:
    ref = ColRef(path="p._id", present="p.c")
    assert ref.expr == {"$cond": [{"$gt": ["$p.c", None]}, "$p._id", None]}
    assert ColRef(path="p._id").expr == "$p._id"


def test_lookup_on_a_left_joined_key_is_null_guarded() -> None:
    p = pipeline(
        "SELECT p.id, c.id, g.id FROM p LEFT JOIN c ON p.id = c.p_id JOIN g ON c.id = g.c_id;"
    )
    lookup = [s["$lookup"] for s in p if "$lookup" in s][-1]
    assert "pipeline" in lookup  # the let/pipeline form with a $gt: [key, null] guard
    assert {"$gt": ["$$v0", None]} in lookup["pipeline"][0]["$match"]["$expr"]["$and"]


def test_embedded_host_lookup_guards_a_nullable_local_key() -> None:
    """c is embedded in p (not in the pipeline): h.c_id may be NULL and must not match every p
    document that merely lacks the embedded field."""
    inner = pipeline("SELECT h.id, c.id FROM h JOIN c ON h.c_id = c.id;", EMBED_C)
    lookup_at = next(i for i, s in enumerate(inner) if "$lookup" in s)
    assert inner[lookup_at - 1] == {
        "$match": {"h.c_id": {"$ne": None}}
    }  # INNER: drop null keys first
    left = pipeline("SELECT h.id, c.id FROM h LEFT JOIN c ON h.c_id = c.id;", EMBED_C)
    at = next(i for i, s in enumerate(left) if "$lookup" in s)
    clear = left[at + 1]["$addFields"]  # LEFT: a null key joins nothing
    ((host, cond),) = clear.items()
    assert cond == {"$cond": [{"$gt": ["$h.c_id", None]}, f"${host}", []]}


def test_not_null_local_key_needs_no_guard() -> None:
    p = pipeline("SELECT g.id, c.id FROM g JOIN c ON g.c_id = c.id;", EMBED_C)
    assert {"$match": {"g.c_id": {"$ne": None}}} not in p


# --------------------------------------------------------------------------- string collation
def test_collections_get_an_english_collation_by_default() -> None:
    result = compile_sql(SCHEMA)
    spec = build_collections(result.schema_, build_layouts(result.graph, result.plan)).collections[
        0
    ]
    assert spec.collation == EN_COLLATION


def test_binary_collation_option_removes_it() -> None:
    result = compile_sql(SCHEMA)
    layouts = build_layouts(result.graph, result.plan)
    for spec in build_collections(result.schema_, layouts, "binary", "binary").collections:
        assert spec.collation is None
    opts = CompileOptions(string_collation="binary")
    code = generate_code(result, opts, timestamp="2026-01-01T00:00:00Z")
    assert (
        "collation"
        not in code.file("mongosh/01_collections.js")
        .content.split("function ensureCollection")[1]
        .split("}\n\n")[1]
    )


def test_generated_scripts_apply_collation_only_at_creation() -> None:
    code = generate_code(compile_sql(SCHEMA), None, timestamp="2026-01-01T00:00:00Z")
    js = code.file("mongosh/01_collections.js").content
    py = code.file("python/schema.py").content
    assert 'collation: {locale: "en"}' in js and "const { collation, ...modifiable }" in js
    assert "'collation': {'locale': 'en'}" in py and 'if k != "collation"' in py
