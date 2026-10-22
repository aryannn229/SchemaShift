"""The fuzz package's pure parts: rendering, generated-case validity, relations over a fake session."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from schemashift.fuzz import relations
from schemashift.fuzz.harness import SelectOutcome
from schemashift.fuzz.spec import (
    TRUE,
    Agg,
    ColSpec,
    FkSpec,
    FuzzCase,
    JoinSpec,
    Pred,
    SchemaSpec,
    SelectSpec,
    TableSpec,
    lit,
    p_and,
    p_not,
    p_or,
    p_unknown,
    render_schema,
    render_seed,
    render_select,
)
from schemashift.fuzz.strategies import cases
from schemashift.models.query import Query
from schemashift.parser import parse
from schemashift.pipeline import compile_sql


# ---------------------------------------------------------------------------- rendering
@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (5, "5"),
        (Decimal("1.50"), "1.50"),
        ("it's", "'it''s'"),
        (dt.date(2020, 1, 2), "'2020-01-02'"),
        (dt.datetime(2020, 1, 2, 3, 4, 5, tzinfo=dt.UTC), "'2020-01-02T03:04:05+00:00'"),
    ],
)
def test_literals(value: Any, text: str) -> None:
    assert lit(value) == text


def test_predicate_rendering() -> None:
    a = Pred(op="cmp", column="t.a", cmp="<>", values=(5,))
    b = Pred(op="in", column="t.b", values=(1, None), negated=True)
    c = Pred(op="between", column="t.c", values=(1, 3), negated=True)
    d = Pred(op="null", column="t.d", negated=True)
    e = Pred(op="like", column="t.e", values=("a%",), ci=True, negated=True)
    f = Pred(op="bool", column="t.f", negated=True)
    g = Pred(op="cmp", column="t.a", cmp="=", rhs_column="t.b")
    assert a.sql() == "t.a <> 5"
    assert b.sql() == "t.b NOT IN (1, NULL)"
    assert c.sql() == "t.c NOT BETWEEN 1 AND 3"
    assert d.sql() == "t.d IS NOT NULL"
    assert e.sql() == "t.e NOT ILIKE 'a%'"
    assert f.sql() == "NOT t.f"
    assert g.sql() == "t.a = t.b"
    assert TRUE.sql() == "TRUE"
    assert p_not(a).sql() == "NOT (t.a <> 5)"
    assert p_and(a, d).sql() == "((t.a <> 5) AND (t.d IS NOT NULL))"
    assert p_or(a, d).sql() == "((t.a <> 5) OR (t.d IS NOT NULL))"
    assert p_unknown(a).sql() == "(t.a <> 5) IS NULL"


SCHEMA = SchemaSpec(
    tables=(
        TableSpec(
            name="t0",
            columns=(
                ColSpec(
                    name="c0", kind="numeric", precision=12, scale=2, nullable=False, unique=True
                ),
                ColSpec(name="c1", kind="varchar", length=10, default="'d'"),
                ColSpec(name="c2", kind="enum", enum_type="mood", check="c2 IS NOT NULL"),
            ),
        ),
        TableSpec(
            name="t1",
            pk=("id", "k2"),
            serial_id=False,
            columns=(ColSpec(name="f0", kind="integer"),),
            fks=(FkSpec(column="f0", ref_table="t0", on_delete="CASCADE", late=True),),
        ),
    ),
    enums={"mood": ("a", "b")},
)


def test_schema_rendering_and_roundtrip_through_the_parser() -> None:
    sql = render_schema(SCHEMA)
    assert "CREATE TYPE mood AS ENUM ('a', 'b');" in sql
    assert "id SERIAL" in sql and "PRIMARY KEY (id, k2)" in sql
    assert "ALTER TABLE t1 ADD CONSTRAINT fk_t1_f0 FOREIGN KEY (f0) REFERENCES t0(id)" in sql
    assert "NUMERIC(12,2) NOT NULL UNIQUE" in sql and "DEFAULT 'd'" in sql
    result = compile_sql(sql)
    assert not result.has_errors


def test_seed_and_select_rendering() -> None:
    seed = render_seed(SCHEMA, {"t0": [{"id": 1, "c0": Decimal("1.00"), "c1": None, "c2": "a"}]})
    assert seed == "INSERT INTO t0 (id, c0, c1, c2) VALUES (1, 1.00, NULL, 'a');\n"
    spec = SelectSpec(
        table="t0",
        alias="a0",
        joins=(JoinSpec(kind="LEFT", table="t1", alias="a1", on_left="a0.id", on_right="a1.f0"),),
        columns=("a0.id",),
        expressions=("a0.id / 2",),
        aggs=(Agg(fn="SUM", column="a0.c0"), Agg(fn="COUNT_STAR")),
        group_by=("a0.id",),
        where=Pred(op="null", column="a0.c1"),
        having="COUNT(*) > 1",
        order_by=(("a0.id", "DESC"),),
        limit=3,
        offset=1,
        distinct=True,
    )
    sql = render_select(spec)
    assert sql == (
        "SELECT DISTINCT a0.id AS c0, a0.id / 2 AS c1, SUM(a0.c0) AS c2, COUNT(*) AS c3 "
        "FROM t0 a0 LEFT JOIN t1 a1 ON a0.id = a1.f0 WHERE a0.c1 IS NULL GROUP BY a0.id "
        "HAVING COUNT(*) > 1 ORDER BY a0.id DESC LIMIT 3 OFFSET 1;"
    )
    assert spec.is_aggregate and not spec.with_where(None).where
    assert render_select(SelectSpec(table="t0", alias="a0")) == "SELECT COUNT(*) AS c0 FROM t0 a0;"
    assert "WHERE" not in render_select(spec.with_where(TRUE))


def test_case_renders_three_sql_texts() -> None:
    case = FuzzCase(
        schema_=SCHEMA,
        rows={},
        selects=(SelectSpec(table="t0", alias="a0", columns=("a0.id",)),),
    )
    assert case.schema_sql().startswith("CREATE TYPE")
    assert case.seed_sql() == ""
    assert case.queries_sql() == "SELECT a0.id AS c0 FROM t0 a0;"


# ------------------------------------------------------------------ generated-case validity
@settings(
    max_examples=80,
    deadline=None,
    database=None,
    suppress_health_check=list(HealthCheck),
)
@given(cases())
def test_generated_cases_are_valid_input(case: FuzzCase) -> None:
    """Every generated case compiles without errors, its seed parses, and each query parses."""
    result = compile_sql(case.schema_sql(), "", case.seed_sql())
    assert not result.has_errors, [d.message for d in result.diagnostics]
    seed = parse(case.seed_sql(), "queries")
    assert not seed.diagnostics
    for spec in case.selects:
        parsed = parse(render_select(spec), "queries")
        assert not parsed.diagnostics, render_select(spec)
        assert all(isinstance(q, Query) and q.kind == "SELECT" for q in parsed.all_queries())


# --------------------------------------------------------------- relations over a fake session
class FakeSession:
    """Answers each SQL from a table; lets the relation logic be tested without databases."""

    def __init__(self, answers: dict[str, list[tuple[Any, ...]]], status: str = "MATCH") -> None:
        self.answers = answers
        self.status = status

    def select(self, sql: str) -> SelectOutcome:
        rows = self.answers.get(sql, [])
        return SelectOutcome(sql=sql, status=self.status, pg=rows, mongo=rows)  # type: ignore[arg-type]


BASE = SelectSpec(table="t0", alias="a0", columns=("a0.id",))
P = Pred(op="cmp", column="a0.id", cmp=">", values=(1,))


def _tlp_queries() -> list[str]:
    plain = BASE.with_where(None)
    return [
        render_select(plain),
        render_select(plain.with_where(P)),
        render_select(plain.with_where(p_not(P))),
        render_select(plain.with_where(p_unknown(P))),
    ]


def test_tlp_passes_when_partitions_add_up() -> None:
    q = _tlp_queries()
    session = FakeSession({q[0]: [(1,), (2,)], q[1]: [(2,)], q[2]: [(1,)], q[3]: []})
    checked, found = relations.tlp(session, BASE, P)  # type: ignore[arg-type]
    assert checked and found == []


def test_tlp_reports_a_violation() -> None:
    q = _tlp_queries()
    session = FakeSession({q[0]: [(1,), (2,)], q[1]: [(2,)], q[2]: [], q[3]: []})
    checked, found = relations.tlp(session, BASE, P)  # type: ignore[arg-type]
    assert checked and found[0].kind == "tlp" and found[0].detail["mongo_counts"] == [2, 1, 0, 0]


def test_relations_skip_untranslatable_and_distinct() -> None:
    session = FakeSession({}, status="NOT_TRANSLATED")
    assert relations.tlp(session, BASE, P) == (False, [])  # type: ignore[arg-type]
    assert relations.tautology(session, BASE, "a0.id") == (False, [])  # type: ignore[arg-type]
    distinct = BASE.model_copy(update={"distinct": True})
    assert relations.tlp(FakeSession({}), distinct, P) == (False, [])  # type: ignore[arg-type]
    assert relations.count_matches_rows(FakeSession({}), distinct) == (False, [])  # type: ignore[arg-type]


def test_commute_needs_a_top_level_and_or() -> None:
    q = BASE.with_where(p_and(P, Pred(op="null", column="a0.id")))
    assert relations.commute(FakeSession({}), BASE) == (False, [])  # type: ignore[arg-type]
    checked, found = relations.commute(FakeSession({}), q)  # type: ignore[arg-type]
    assert checked and found == []


def test_commute_and_tautology_report_differences() -> None:
    w = p_and(P, Pred(op="null", column="a0.id"))
    spec = BASE.with_where(w)
    plain = spec.model_copy(update={"order_by": (), "limit": None, "offset": None})
    flipped = plain.with_where(w.model_copy(update={"items": tuple(reversed(w.items))}))
    session = FakeSession({render_select(plain): [(1,)], render_select(flipped): []})
    checked, found = relations.commute(session, spec)  # type: ignore[arg-type]
    assert checked and found[0].kind == "commute"

    taut_spec = BASE.with_where(None)
    session2 = FakeSession({render_select(taut_spec): [(1,), (2,)]})
    # the tautology query is not in the table: it answers with no rows, so the result differs
    checked, found = relations.tautology(session2, taut_spec, "a0.id")  # type: ignore[arg-type]
    assert checked and found[0].kind == "tautology"


def test_count_relation() -> None:
    plain = BASE.with_where(None)
    rows_sql = render_select(plain)
    count_sql = render_select(plain.model_copy(update={"columns": ()}))
    ok = FakeSession({rows_sql: [(1,), (2,)], count_sql: [(2,)]})
    assert relations.count_matches_rows(ok, BASE) == (True, [])  # type: ignore[arg-type]
    bad = FakeSession({rows_sql: [(1,)], count_sql: [(3,)]})
    checked, found = relations.count_matches_rows(bad, BASE)  # type: ignore[arg-type]
    assert checked and found[0].detail == {"count": 3, "rows": 1}
