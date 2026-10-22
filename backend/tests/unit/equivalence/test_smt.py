"""Phase 26 (pure parts): the supported fragment, three-valued logic, rounding, overflow and honesty."""

from __future__ import annotations

import pytest

from schemashift.codegen.collections import build_collections
from schemashift.codegen.layout import build_layouts
from schemashift.equivalence.smt import run_smt
from schemashift.pipeline import CompileOptions, compile_sql


def smt(sql: str, **kw: object):  # type: ignore[no-untyped-def]
    result = compile_sql(sql, "", "", CompileOptions(smt=False))
    return result, run_smt(result, **kw)  # type: ignore[arg-type]


def table(report, name="t"):  # type: ignore[no-untyped-def]
    return next(t for t in report.tables if t.table == name)


def test_simple_checks_are_proven_equivalent() -> None:
    _, rep = smt(
        "CREATE TABLE t (id INT PRIMARY KEY, n INT NOT NULL CHECK (n BETWEEN 1 AND 10), "
        "s VARCHAR(5), CHECK (LENGTH(s) >= 2 OR s IS NULL));"
    )
    t = table(rep)
    assert t.status == "EQUIVALENT" and "check:t.n_BETWEEN_1_AND_10" in t.encoded
    assert "not_null:t.n" in t.encoded and "type:t.s" in t.encoded


def test_a_null_passes_a_check_like_in_sql_and_in_the_generated_guard() -> None:
    # SQL: CHECK passes on UNKNOWN. The generated $expr must guard the null/missing case.
    _, rep = smt("CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, CHECK (a < b));")
    assert table(rep).status == "EQUIVALENT"


def test_numeric_precision_overflow_is_a_counterexample_with_the_row() -> None:
    _, rep = smt("CREATE TABLE t (id INT PRIMARY KEY, price NUMERIC(6,2) CHECK (price >= 0));")
    t = table(rep)
    assert t.status == "COUNTEREXAMPLE"
    f = next(x for x in t.findings if x.direction == "mongo_accepts_sql_rejects")
    assert f.nodes == ("type:t.price",) and f.row["price"] is not None
    assert abs(float(f.row["price"])) >= 9999.995  # does not fit NUMERIC(6,2) after rounding


def test_rounding_happens_before_the_check() -> None:
    # 0.005 rounds to 0 in NUMERIC(10,0), so PostgreSQL accepts it although 0.005 > 0 fails the CHECK
    _, rep = smt("CREATE TABLE t (id INT PRIMARY KEY, c NUMERIC(10,0) NOT NULL CHECK (c <= 0));")
    t = table(rep)
    rows = [f.row["c"] for f in t.findings if f.direction == "sql_accepts_mongo_rejects"]
    assert rows and 0 < float(rows[0]) < 0.5


def test_integer_overflow_in_arithmetic_is_modelled() -> None:
    # c * 2 raises in PostgreSQL for large c; MongoDB has no 32-bit overflow
    _, rep = smt("CREATE TABLE t (id INT PRIMARY KEY, c INT NOT NULL CHECK (c * 2 >= 0));")
    t = table(rep)
    assert t.status == "COUNTEREXAMPLE"
    row = next(f.row for f in t.findings if f.direction == "mongo_accepts_sql_rejects")
    assert int(row["c"] or 0) > 2**30 - 1


def test_unsupported_constructs_are_unverified_never_safe() -> None:
    result, rep = smt("CREATE TABLE t (id INT PRIMARY KEY, a INT, CHECK (a % 2 = 0));")
    t = table(rep)
    assert t.status == "UNSUPPORTED" and t.encoded == () and t.reason
    full = compile_sql("CREATE TABLE t (id INT PRIMARY KEY, a INT, CHECK (a % 2 = 0));")
    v = next(v for v in full.equivalence.final.verdicts if v.node_id.startswith("check:t."))
    assert v.evidence != "PROVEN"  # the solver could not decide: no upgrade


def test_embedded_tables_are_reported_as_outside_the_fragment() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), n INT CHECK (n > 0));"
    )
    result = compile_sql(
        sql, "", "", CompileOptions(smt=False, relationship_overrides={"fk:c.p_id->p.id": "EMBED"})
    )  # type: ignore[dict-item]
    rep = run_smt(result)
    assert table(rep, "c").status == "UNSUPPORTED" and "embedded" in table(rep, "c").reason


def test_timeout_is_reported_not_guessed() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, CHECK (a * b > 1000000007 OR a < b));"
    _, rep = smt(sql, timeout_ms=1)
    assert table(rep).status in (
        "TIMEOUT",
        "EQUIVALENT",
        "COUNTEREXAMPLE",
    )  # never an unchecked claim
    assert table(rep).status != "EQUIVALENT" or table(rep).encoded


def test_compile_applies_the_proof_to_the_verdicts() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, n INT NOT NULL CHECK (n > 0));"
    on = compile_sql(sql)
    off = compile_sql(sql, "", "", CompileOptions(smt=False))
    v_on = next(v for v in on.equivalence.final.verdicts if v.node_id == "not_null:t.n")
    v_off = next(v for v in off.equivalence.final.verdicts if v.node_id == "not_null:t.n")
    assert v_off.evidence == "CONDITIONAL" and v_on.evidence == "PROVEN"
    assert v_on.status == v_off.status and "z3" in (v_on.argument or "")
    assert {c.kind for c in v_on.evidence_conditions} == {"validators_installed", "null_as_absent"}
    assert on.smt is not None and on.smt.proven_nodes


def test_counterexample_raises_a_safe_verdict_and_names_the_row() -> None:
    # decimal columns lose precision checks in the validator: the type verdict must not stay clean
    r = compile_sql("CREATE TABLE t (id INT PRIMARY KEY, p NUMERIC(6,2) NOT NULL);")
    v = next(v for v in r.equivalence.final.verdicts if v.node_id == "type:t.p")
    assert v.status in ("CHANGED", "BROKEN") and "Z3 counterexample" in v.reason


def test_contradictory_in_lists_no_longer_produce_an_invalid_validator() -> None:
    """Found by the soundness test: two IN checks with an empty intersection gave `enum: []`."""
    r = compile_sql(
        "CREATE TABLE t (id INT PRIMARY KEY, c INT NOT NULL, CHECK (c IN (0, 3)), CHECK (c IN (1, 4)));",
        "",
        "",
        CompileOptions(smt=False),
    )
    spec = build_collections(r.schema_, build_layouts(r.graph, r.plan)).collections[0]
    props = (
        spec.validator["$and"][0]["$jsonSchema"]["properties"]["c"]
        if "$and" in spec.validator
        else spec.validator["$jsonSchema"]["properties"]["c"]
    )
    assert "enum" not in props and props["not"] == {}
    rep = run_smt(r)
    assert table(rep).status == "EQUIVALENT"  # PostgreSQL accepts no non-null value either


@pytest.mark.parametrize(
    "check",
    ["a IS NOT NULL", "a <> 3", "NOT (a < 1)", "a IN (1, 2) OR b IS NULL", "a + b > 0"],
)
def test_common_checks_stay_inside_the_fragment(check: str) -> None:
    _, rep = smt(f"CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, CHECK ({check}));")
    assert table(rep).status in ("EQUIVALENT", "COUNTEREXAMPLE")


def test_length_check_guard_tests_the_column_not_the_raising_function() -> None:
    """Found by the soundness test: `$lte: [{$strLenCP: "$s"}, null]` raises on a missing s, so the
    generated validator rejected rows PostgreSQL accepts (s NULL)."""
    import json

    r = compile_sql(
        "CREATE TABLE t (id INT PRIMARY KEY, s VARCHAR(5), CHECK (LENGTH(s) >= 2 OR s IS NULL));",
        "",
        "",
        CompileOptions(smt=False),
    )
    spec = build_collections(r.schema_, build_layouts(r.graph, r.plan)).collections[0]
    text = json.dumps(spec.validator)
    assert '{"$lte": [{"$strLenCP"' not in text and '{"$gte": [{"$strLenCP": "$s"}, 2]}' in text
    assert run_smt(r).tables[0].status == "EQUIVALENT"


def test_the_solver_finds_a_strlen_validator_that_raises_on_missing_fields() -> None:
    """The same defect expressed as a hand-written validator: Z3 must report it."""
    import z3

    from schemashift.equivalence.smt.mongo import MongoExpr
    from schemashift.equivalence.smt.sql import make_var
    from schemashift.models.schema import Column, NormalizedType

    col = Column(name="s", sql_type=NormalizedType(base="TEXT"))
    v = make_var(col)
    bad = {"$or": [{"$lte": [{"$strLenCP": "$s"}, None]}, {"$gte": [{"$strLenCP": "$s"}, 2]}]}
    s = z3.Solver()
    s.add(z3.Not(v.present), MongoExpr({"s": v}).truth(bad))
    assert s.check() == z3.unsat  # a row with s missing can never satisfy it: the error rejects it
