import pytest
import sqlglot

from schemashift.codegen.predicate import (
    ColRef,
    PAnd,
    PBool,
    PCmp,
    PConst,
    PIn,
    PLike,
    PNull,
    POr,
    Untranslatable,
    conjuncts,
    like_to_regex,
    literal_value,
    parse_predicate,
    parse_timestamp,
    to_expr,
    to_query,
    value_expr,
)
from schemashift.codegen.values import DateValue, RegexValue, UuidValue
from schemashift.models import NormalizedType

INT = NormalizedType(base="INTEGER")
TS = NormalizedType(base="TIMESTAMPTZ")


def resolve(col):  # type: ignore[no-untyped-def]
    name = str(col.name)
    types = {"d": TS, "f": NormalizedType(base="DOUBLE"), "u": NormalizedType(base="UUID")}
    return ColRef(path=name, type=types.get(name, INT))


def pred(sql: str):  # type: ignore[no-untyped-def]
    return parse_predicate(sqlglot.parse_one(sql, dialect="postgres"))


def query(sql: str):  # type: ignore[no-untyped-def]
    return to_query(pred(sql), resolve)


def expr(sql: str, mode: str = "where"):  # type: ignore[no-untyped-def]
    return to_expr(pred(sql), resolve, mode)  # type: ignore[arg-type]


# ------------------------------------------------------------------------ parsing
def test_parse_shapes() -> None:
    assert isinstance(pred("a > 1 AND b < 2"), PAnd)
    assert isinstance(pred("a > 1 OR b < 2"), POr)
    assert isinstance(pred("a IN (1, 2)"), PIn)
    assert isinstance(pred("a LIKE 'x%'"), PLike)
    assert isinstance(pred("a IS NULL"), PNull)
    assert isinstance(pred("a"), PBool)
    assert isinstance(pred("TRUE"), PConst)
    assert isinstance(pred("a BETWEEN 1 AND 5"), PAnd)


@pytest.mark.parametrize(
    ("sql", "op"),
    [
        ("NOT (a = 1)", "ne"),
        ("NOT (a <> 1)", "eq"),
        ("NOT (a < 1)", "gte"),
        ("NOT (a <= 1)", "gt"),
        ("NOT (a > 1)", "lte"),
        ("NOT (a >= 1)", "lt"),
    ],
)
def test_negation_is_pushed_into_comparisons(sql: str, op: str) -> None:
    p = pred(sql)
    assert isinstance(p, PCmp) and p.op == op


def test_de_morgan() -> None:
    p = pred("NOT (a = 1 AND b = 2)")
    assert isinstance(p, POr) and all(isinstance(i, PCmp) and i.op == "ne" for i in p.items)
    p = pred("NOT (a = 1 OR b = 2)")
    assert isinstance(p, PAnd)


def test_not_between_is_or_of_outside_ranges() -> None:
    p = pred("a NOT BETWEEN 1 AND 5")
    assert isinstance(p, POr)
    assert [i.op for i in p.items] == ["lt", "gt"]  # type: ignore[union-attr]


def test_double_negation() -> None:
    assert pred("NOT (NOT (a = 1))").op == "eq"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "sql", ["a IN (SELECT 1)", "a IS TRUE", "UPPER(a) = 'X'", "a || 'x' = 'y'"]
)
def test_unparseable_predicates(sql: str) -> None:
    with pytest.raises(Untranslatable):
        to_expr(pred(sql), resolve, "where")


# ----------------------------------------------------------------------- literals
def test_like_to_regex() -> None:
    assert like_to_regex("a%") == "^a.*$"
    assert like_to_regex("a_c") == "^a.c$"
    assert like_to_regex("100\\%") == "^100%$"
    assert like_to_regex("a.b") == "^a\\.b$"


def test_literal_conversion() -> None:
    n = lambda s: sqlglot.parse_one(s, dialect="postgres")  # noqa: E731
    assert literal_value(n("5"), INT) == 5
    assert literal_value(n("5"), NormalizedType(base="DOUBLE")) == 5.0 and isinstance(
        literal_value(n("5"), NormalizedType(base="DOUBLE")), float
    )
    assert literal_value(n("-2.5"), None) == -2.5
    assert literal_value(n("'x'"), None) == "x"
    assert literal_value(n("TRUE"), None) is True
    assert literal_value(n("NULL"), None) is None
    assert literal_value(n("'2024-01-02'"), TS) == DateValue("2024-01-02T00:00:00+00:00")
    assert literal_value(n("'u'"), NormalizedType(base="UUID")) == UuidValue("u")
    with pytest.raises(Untranslatable):
        literal_value(n("'not a date'"), TS)
    with pytest.raises(Untranslatable):
        literal_value(n("a + 1"), None)


def test_parse_timestamp_normalises_to_utc() -> None:
    assert parse_timestamp("2024-01-01 10:00:00+02:00") == "2024-01-01T08:00:00+00:00"
    assert parse_timestamp("2024-01-01T00:00:00Z") == "2024-01-01T00:00:00+00:00"


def test_conjuncts() -> None:
    assert len(conjuncts(sqlglot.parse_one("a = 1 AND (b = 2 AND c = 3) AND d = 4"))) == 4


# --------------------------------------------------------------------- query form
def test_query_simple_comparisons() -> None:
    assert query("a = 5") == {"a": 5}
    assert query("a > 5") == {"a": {"$gt": 5}}
    assert query("a <= 5") == {"a": {"$lte": 5}}
    assert query("5 < a") == {"a": {"$gt": 5}}  # operands flipped


def test_query_not_equal_excludes_nulls() -> None:
    assert query("a <> 5") == {"a": {"$nin": [5, None]}}


def test_query_in_and_not_in() -> None:
    assert query("a IN (1, 2)") == {"a": {"$in": [1, 2]}}
    assert query("a NOT IN (1, 2)") == {"a": {"$nin": [1, 2, None]}}
    assert query("a NOT IN (1, NULL)") == {"$expr": False}


def test_query_null_checks() -> None:
    assert query("a IS NULL") == {"a": None}
    assert query("a IS NOT NULL") == {"a": {"$ne": None}}


def test_query_like() -> None:
    assert query("a LIKE 'x%'") == {"a": {"$regex": "^x.*$", "$options": "s"}}
    assert query("a ILIKE 'x%'")["a"]["$options"] == "si"
    neg = query("a NOT LIKE 'x%'")
    assert neg["$and"][0] == {"a": {"$ne": None}}
    assert neg["$and"][1]["a"]["$not"] == RegexValue("^x.*$", "s")


def test_query_boolean_column() -> None:
    assert query("a") == {"a": True}
    assert query("NOT a") == {"a": False}


def test_query_logic() -> None:
    assert query("a = 1 AND b = 2") == {"$and": [{"a": 1}, {"b": 2}]}
    assert query("a = 1 OR b = 2") == {"$or": [{"a": 1}, {"b": 2}]}
    assert query("NOT (a = 1 AND b = 2)") == {
        "$or": [{"a": {"$nin": [1, None]}}, {"b": {"$nin": [2, None]}}]
    }


def test_query_between() -> None:
    assert query("a BETWEEN 1 AND 5") == {"$and": [{"a": {"$gte": 1}}, {"a": {"$lte": 5}}]}


def test_query_dates_are_converted_by_column_type() -> None:
    assert query("d >= '2024-01-01'") == {"d": {"$gte": DateValue("2024-01-01T00:00:00+00:00")}}


def test_query_falls_back_to_expr_for_cross_column() -> None:
    q = query("a < b")
    assert list(q) == ["$expr"]


def test_query_constants() -> None:
    assert query("TRUE") == {}
    assert query("FALSE") == {"$expr": False}
    assert query("a = NULL") == {"$expr": {"$and": [{"$eq": ["$a", None]}]}} or "$expr" in query(
        "a = NULL"
    )


# -------------------------------------------------------------------- expression form
def test_where_mode_guards_nulls() -> None:
    assert expr("a > 5") == {"$and": [{"$gt": ["$a", None]}, {"$gt": ["$a", 5]}]}


def test_where_mode_cross_column_guards_both_sides() -> None:
    e = expr("a < b")
    assert e["$and"][:2] == [{"$gt": ["$a", None]}, {"$gt": ["$b", None]}]


def test_check_mode_accepts_unknown() -> None:
    assert expr("a > 5", "check") == {"$or": [{"$lte": ["$a", None]}, {"$gt": ["$a", 5]}]}


def test_check_mode_not_pushes_negation_before_null_handling() -> None:
    # NOT (a > 5) must still accept NULL: it becomes a <= 5 with the null guard
    assert expr("NOT (a > 5)", "check") == {"$or": [{"$lte": ["$a", None]}, {"$lte": ["$a", 5]}]}


def test_check_mode_unknown_constants() -> None:
    assert expr("NULL", "check") is True
    assert expr("NULL", "where") is False
    assert expr("a = NULL", "check") is True
    assert expr("a = NULL", "where") is False


def test_expr_in_and_like() -> None:
    assert expr("a IN (1, 2)") == {"$and": [{"$gt": ["$a", None]}, {"$in": ["$a", [1, 2]]}]}
    assert expr("a IN (1, 2)", "check") == {
        "$or": [{"$lte": ["$a", None]}, {"$in": ["$a", [1, 2]]}]
    }
    like = expr("a LIKE 'x%'")
    assert like == {"$regexMatch": {"input": "$a", "regex": "^x.*$", "options": "s"}}
    assert expr("a NOT LIKE 'x%'")["$and"][0] == {"$gt": ["$a", None]}


def test_expr_not_in_with_null_list() -> None:
    assert expr("a NOT IN (1, NULL)", "where") is False
    assert "$or" in expr("a NOT IN (1, NULL)", "check")


def test_expr_null_tests_and_boolean() -> None:
    assert expr("a IS NULL") == {"$lte": ["$a", None]}
    assert expr("a IS NOT NULL") == {"$gt": ["$a", None]}
    assert expr("a") == {"$eq": ["$a", True]}
    assert expr("a", "check") == {"$ne": ["$a", False]}


def test_value_expressions() -> None:
    n = lambda s: sqlglot.parse_one(s, dialect="postgres")  # noqa: E731
    assert value_expr(n("a + 1"), resolve) == {"$add": ["$a", 1]}
    assert value_expr(n("a * b - 2"), resolve) == {"$subtract": [{"$multiply": ["$a", "$b"]}, 2]}
    assert value_expr(n("a / 2"), resolve) == {"$divide": ["$a", 2]}
    assert value_expr(n("-a"), resolve) == {"$multiply": [-1, "$a"]}
    assert value_expr(n("LENGTH(a)"), resolve) == {"$strLenCP": "$a"}
    assert value_expr(n("'$x'"), resolve) == {"$literal": "$x"}
    assert value_expr(n("(a)"), resolve) == "$a"
    with pytest.raises(Untranslatable):
        value_expr(n("UPPER(a)"), resolve)


def test_hook_can_claim_nodes() -> None:
    n = sqlglot.parse_one("a + 1", dialect="postgres")
    from schemashift.codegen.predicate import NO_HOOK

    out = value_expr(n, resolve, None, lambda node: "$$X" if node.sql() == "a" else NO_HOOK)
    assert out == {"$add": ["$$X", 1]}


def test_arithmetic_comparison_uses_column_type_hint() -> None:
    e = expr("d > '2024-01-01'")
    assert e["$and"][1] == {"$gt": ["$d", DateValue("2024-01-01T00:00:00+00:00")]}
