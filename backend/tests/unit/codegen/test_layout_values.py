from datetime import UTC, datetime
from decimal import Decimal

import pytest

from schemashift.codegen.layout import children_of, pluralize_id, roots
from schemashift.codegen.values import (
    DateValue,
    DecimalValue,
    NowValue,
    RawJs,
    RegexValue,
    UuidValue,
    js_string,
    materialize,
    to_js,
    to_py,
)
from tests.unit.codegen.helpers import compile_with_plan

P = "CREATE TABLE p (id INT PRIMARY KEY, code TEXT);"
C = "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), v TEXT);"
G = "CREATE TABLE g (id INT PRIMARY KEY, c_id INT NOT NULL REFERENCES c(id));"
JUNC = (
    "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
    "CREATE TABLE ab (a_id INT NOT NULL REFERENCES a(id), b_id INT NOT NULL REFERENCES b(id), {extra}"
    "PRIMARY KEY (a_id, b_id));"
)


# ------------------------------------------------------------------------ layouts
def test_everything_is_root_by_default() -> None:
    _, lay = compile_with_plan(P + C)
    assert all(x.is_root for x in lay.values())
    assert lay["p"].id_column == "id" and lay["p"].fields["id"] == "_id"
    assert lay["c"].fields == {"id": "_id", "p_id": "p_id", "v": "v"}


def test_array_embedding() -> None:
    _, lay = compile_with_plan(P + C, plan={"fk:c.p_id->p.id": "EMBED"})
    c = lay["c"]
    assert (c.kind, c.collection, c.host, c.path) == ("array", "p", "p", ("c",))
    assert c.dropped_columns == ("p_id",) and c.host_columns == ("id",)
    assert c.fields == {"id": "id", "v": "v"}  # PK of an embedded row keeps its name
    assert c.dotted("v") == "c.v" and c.is_array and not c.is_root
    assert [x.table for x in roots(lay)] == ["p"]
    assert [x.table for x in children_of(lay, "p")] == ["c"]


def test_one_to_one_embedding_is_an_object() -> None:
    sql = P + "CREATE TABLE c (p_id INT PRIMARY KEY REFERENCES p(id), v TEXT);"
    _, lay = compile_with_plan(sql, plan={"fk:c.p_id->p.id": "EMBED"})
    assert lay["c"].kind == "object" and lay["c"].path == ("c",)


def test_nested_embedding_path() -> None:
    _, lay = compile_with_plan(
        P + C + G, plan={"fk:c.p_id->p.id": "EMBED", "fk:g.c_id->c.id": "EMBED"}
    )
    assert lay["g"].path == ("c", "g") and lay["g"].collection == "p" and lay["g"].host == "c"
    assert lay["g"].dotted("id") == "c.g.id"


def test_embedded_name_collision_is_resolved() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY, c INT); " + C
    _, lay = compile_with_plan(sql, plan={"fk:c.p_id->p.id": "EMBED"})
    assert lay["c"].path == ("c_docs",)


def test_pure_junction_folds_into_scalar_array() -> None:
    _, lay = compile_with_plan(JUNC.format(extra=""), plan={"m2n:ab": "REF_ARRAY"})
    # the planner-less plan has no host, so it stays a root; give it one
    from schemashift.codegen.layout import build_layouts
    from schemashift.models import PlacementDecision, PlacementPlan

    result, _ = compile_with_plan(JUNC.format(extra=""))
    plan = PlacementPlan(
        decisions={
            "m2n:ab": PlacementDecision(relationship_id="m2n:ab", decision="REF_ARRAY", host="a")
        }
    )
    lay = build_layouts(result.graph, plan)
    ab = lay["ab"]
    assert ab.kind == "ref_scalars" and ab.collection == "a" and ab.path == ("b_ids",)
    assert ab.scalar_column == "b_id" and ab.dropped_columns == ("a_id",)
    assert ab.dotted("b_id") == "b_ids" and ab.has_column("b_id")


def test_junction_with_payload_folds_into_documents() -> None:
    from schemashift.codegen.layout import build_layouts
    from schemashift.models import PlacementDecision, PlacementPlan

    result, _ = compile_with_plan(JUNC.format(extra="qty INT, "))
    plan = PlacementPlan(
        decisions={
            "m2n:ab": PlacementDecision(relationship_id="m2n:ab", decision="REF_ARRAY", host="b")
        }
    )
    ab = build_layouts(result.graph, plan)["ab"]
    assert ab.kind == "ref_docs" and ab.collection == "b" and ab.path == ("ab",)
    assert set(ab.fields) == {"a_id", "qty"} and ab.dropped_columns == ("b_id",)


def test_composite_pk_root_has_generated_id() -> None:
    _, lay = compile_with_plan("CREATE TABLE t (a INT, b INT, v TEXT, PRIMARY KEY (a, b));")
    assert lay["t"].id_column is None and lay["t"].fields == {"a": "a", "b": "b", "v": "v"}


def test_no_primary_key_root() -> None:
    _, lay = compile_with_plan("CREATE TABLE t (a INT);")
    assert lay["t"].id_column is None


def test_self_reference_is_never_embedded() -> None:
    _, lay = compile_with_plan(
        "CREATE TABLE e (id INT PRIMARY KEY, m INT REFERENCES e(id));",
        plan={"fk:e.m->e.id": "EMBED"},
    )
    assert lay["e"].is_root


def test_pluralize_id() -> None:
    assert pluralize_id("product_id") == "product_ids"


# ------------------------------------------------------------------------- values
def test_js_primitives() -> None:
    assert (
        to_js({"a": 1, "b": "x", "c": None, "d": True, "e": [1, 2.5]})
        == '{a: 1, b: "x", c: null, d: true, e: [1, 2.5]}'
    )


def test_js_keys_are_quoted_only_when_needed() -> None:
    assert (
        to_js({"$match": 1, "a.b": 2, "_id": 3, "1x": 4})
        == '{$match: 1, "a.b": 2, _id: 3, "1x": 4}'
    )


def test_js_wrappers() -> None:
    assert to_js(DateValue("2024-01-01T00:00:00+00:00")) == 'new Date("2024-01-01T00:00:00+00:00")'
    assert to_js(NowValue()) == "new Date()"
    assert to_js(DecimalValue("1.50")) == 'NumberDecimal("1.50")'
    assert to_js(Decimal("2.5")) == 'NumberDecimal("2.5")'
    assert (
        to_js(UuidValue("00000000-0000-0000-0000-000000000001"))
        == 'UUID("00000000-0000-0000-0000-000000000001")'
    )
    assert to_js(RegexValue("^a/b$", "s")) == "/^a\\/b$/s"
    assert to_js(RawJs("db.x", "db_x")) == "db.x"


def test_js_string_escaping() -> None:
    assert js_string('a"b\\c\nd\te\x01') == '"a\\"b\\\\c\\nd\\te\\u0001"'


def test_js_wraps_long_values() -> None:
    text = to_js({"k": ["x" * 40, "y" * 40, "z" * 40]})
    assert "\n" in text and text.startswith("{\n")


def test_js_rejects_unknown_types() -> None:
    with pytest.raises(TypeError):
        to_js(object())


def test_python_rendering() -> None:
    assert to_py({"a": 1, "b": None, "c": True}) == "{'a': 1, 'b': None, 'c': True}"
    assert (
        to_py(DateValue("2024-01-01T00:00:00+00:00"))
        == "datetime.fromisoformat('2024-01-01T00:00:00+00:00')"
    )
    assert to_py(NowValue()) == "datetime.now(timezone.utc)"
    assert to_py(DecimalValue("1.5")) == "Decimal128('1.5')"
    assert to_py(UuidValue("u")) == "uuid.UUID('u')"
    assert to_py(RegexValue("a", "s")) == "Regex('a', 's')"
    assert to_py(RawJs("js", "py")) == "py"
    assert to_py((1, 2)) == "[1, 2]"
    with pytest.raises(TypeError):
        to_py(object())


def test_python_wraps_long_values() -> None:
    text = to_py({"k": ["x" * 40, "y" * 40, "z" * 40]})
    assert text.startswith("{\n") and text.rstrip().endswith("}")


def test_materialize_builds_driver_objects() -> None:
    from bson import Binary, Decimal128
    from bson.regex import Regex

    out = materialize(
        {
            "d": DateValue("2024-01-01T00:00:00+00:00"),
            "n": DecimalValue("1.5"),
            "r": RegexValue("a", "s"),
            "u": UuidValue("00000000-0000-0000-0000-000000000001"),
            "l": [Decimal("2")],
            "now": NowValue(),
        }
    )
    assert out["d"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert isinstance(out["n"], Decimal128) and isinstance(out["r"], Regex)
    assert isinstance(out["u"], Binary) and isinstance(out["l"][0], Decimal128)
    assert out["now"].tzinfo is not None
