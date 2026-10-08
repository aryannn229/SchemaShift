from typing import Any

import pytest

from schemashift.codegen.collections import bson_schema, build_collections
from schemashift.codegen.indexes import build_indexes, partial_filter
from schemashift.models import NormalizedType, Placement
from tests.unit.codegen.helpers import compile_with_plan


def validator(sql: str, table: str, plan: dict[str, Placement] | None = None) -> dict[str, Any]:
    result, layouts = compile_with_plan(sql, plan=plan)
    cols = build_collections(result.schema_, layouts)
    spec = next(c for c in cols.collections if c.table == table)
    return spec.validator


def schema_of(sql: str, table: str, plan: dict[str, Placement] | None = None) -> dict[str, Any]:
    v = validator(sql, table, plan)
    return v["$jsonSchema"] if "$jsonSchema" in v else v["$and"][0]["$jsonSchema"]


def indexes(sql: str, plan: dict[str, Placement] | None = None):  # type: ignore[no-untyped-def]
    result, layouts = compile_with_plan(sql, plan=plan)
    return build_indexes(result.schema_, result.graph, layouts)


# --------------------------------------------------------------------- types
@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("INT", {"bsonType": "int"}),
        ("SMALLINT", {"bsonType": "int", "minimum": -32768, "maximum": 32767}),
        ("BIGINT", {"bsonType": ["int", "long"]}),
        ("NUMERIC(10,2)", {"bsonType": "decimal"}),
        ("REAL", {"bsonType": "number"}),
        ("BOOLEAN", {"bsonType": "bool"}),
        ("TEXT", {"bsonType": "string"}),
        ("VARCHAR(20)", {"bsonType": "string", "maxLength": 20}),
        ("CHAR(3)", {"bsonType": "string", "maxLength": 3}),
        ("UUID", {"bsonType": "binData"}),
        ("DATE", {"bsonType": "date"}),
        ("TIMESTAMPTZ", {"bsonType": "date"}),
        ("TIME", {"bsonType": "string"}),
        ("INTERVAL", {"bsonType": "string"}),
        ("JSONB", {}),
        ("BYTEA", {"bsonType": "binData"}),
        ("INT[]", {"bsonType": "array", "items": {"bsonType": "int"}}),
    ],
)
def test_type_mapping(sql_type: str, expected: dict[str, Any]) -> None:
    props = schema_of(f"CREATE TABLE t (id INT PRIMARY KEY, c {sql_type});", "t")["properties"]
    assert props["c"] == expected


def test_uuid_as_string() -> None:
    result, _ = compile_with_plan("CREATE TABLE t (u UUID);")
    assert bson_schema(NormalizedType(base="UUID"), result.schema_, "string") == {
        "bsonType": "string"
    }


def test_enum_values() -> None:
    sql = "CREATE TYPE m AS ENUM ('a', 'b'); CREATE TABLE t (id INT PRIMARY KEY, c m);"
    assert schema_of(sql, "t")["properties"]["c"] == {"bsonType": "string", "enum": ["a", "b"]}


# ----------------------------------------------------------------- required / ids
def test_not_null_is_required_and_nullable_is_optional() -> None:
    s = schema_of("CREATE TABLE t (id INT PRIMARY KEY, a TEXT NOT NULL, b TEXT);", "t")
    assert s["required"] == ["a"]
    assert s["properties"]["b"] == {"bsonType": "string"}  # no "null": NULL = absent field
    assert s["additionalProperties"] is False


def test_single_pk_becomes_id() -> None:
    s = schema_of("CREATE TABLE t (id BIGINT PRIMARY KEY, a TEXT);", "t")
    assert s["properties"]["_id"] == {"bsonType": ["int", "long"]} and "id" not in s["properties"]


def test_composite_or_missing_pk_uses_object_id() -> None:
    s = schema_of("CREATE TABLE t (a INT, b INT, PRIMARY KEY (a, b));", "t")
    assert s["properties"]["_id"] == {"bsonType": "objectId"}
    assert set(s["required"]) == {"a", "b"}
    s = schema_of("CREATE TABLE t (a INT);", "t")
    assert s["properties"]["_id"] == {"bsonType": "objectId"}


def test_validation_options() -> None:
    result, layouts = compile_with_plan("CREATE TABLE t (id INT PRIMARY KEY);")
    spec = build_collections(result.schema_, layouts).collections[0]
    assert (spec.validation_level, spec.validation_action) == ("strict", "error")
    assert "_id = t.id" in spec.id_description


# --------------------------------------------------------------------- checks
def test_simple_checks_become_json_schema_keywords() -> None:
    s = schema_of(
        "CREATE TABLE t (id INT PRIMARY KEY, a INT CHECK (a >= 0), b INT CHECK (b > 0 AND b < 10),"
        " c TEXT CHECK (c IN ('x', 'y')), d TEXT CHECK (LENGTH(d) >= 3), e TEXT CHECK (e LIKE 'a%'),"
        " f INT CHECK (f BETWEEN 1 AND 5), g TEXT CHECK (g = 'z'));",
        "t",
    )["properties"]
    assert s["a"]["minimum"] == 0
    assert (
        s["b"]["minimum"],
        s["b"]["exclusiveMinimum"],
        s["b"]["maximum"],
        s["b"]["exclusiveMaximum"],
    ) == (0, True, 10, True)
    assert s["c"]["enum"] == ["x", "y"]
    assert s["d"]["minLength"] == 3
    assert s["e"]["pattern"] == "^a.*$"
    assert (s["f"]["minimum"], s["f"]["maximum"]) == (1, 5)
    assert s["g"]["enum"] == ["z"]


def test_length_bounds_on_integers_are_inclusive() -> None:
    s = schema_of(
        "CREATE TABLE t (id INT PRIMARY KEY, d TEXT CHECK (LENGTH(d) > 2 AND LENGTH(d) < 9));", "t"
    )
    assert (s["properties"]["d"]["minLength"], s["properties"]["d"]["maxLength"]) == (3, 8)


def test_cross_column_check_becomes_expr() -> None:
    v = validator("CREATE TABLE t (id INT PRIMARY KEY, lo INT, hi INT, CHECK (lo <= hi));", "t")
    assert "$and" in v and "$expr" in v["$and"][1]
    assert v["$and"][1]["$expr"] == {
        "$or": [{"$lte": ["$lo", None]}, {"$lte": ["$hi", None]}, {"$lte": ["$lo", "$hi"]}]
    }


def test_mixed_and_splits_between_keywords_and_expr() -> None:
    v = validator(
        "CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, CHECK (a > 0 AND a < b));", "t"
    )
    props = v["$and"][0]["$jsonSchema"]["properties"]
    assert props["a"]["minimum"] == 0 and props["a"]["exclusiveMinimum"] is True
    assert len(v["$and"]) == 2


def test_date_comparison_uses_expr_not_keywords() -> None:
    v = validator("CREATE TABLE t (id INT PRIMARY KEY, d DATE CHECK (d >= '2020-01-01'));", "t")
    assert "$and" in v and "minimum" not in v["$and"][0]["$jsonSchema"]["properties"]["d"]


def test_untranslatable_check_is_reported() -> None:
    result, layouts = compile_with_plan(
        "CREATE TABLE t (id INT PRIMARY KEY, a TEXT, CONSTRAINT ck CHECK (UPPER(a) = a));"
    )
    cols = build_collections(result.schema_, layouts)
    assert [(u.table, u.name) for u in cols.untranslated] == [("t", "ck")]
    assert "$and" not in cols.collections[0].validator


# --------------------------------------------------------------------- embedded
EMB = (
    "CREATE TABLE p (id INT PRIMARY KEY, code TEXT);"
    "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), n INT NOT NULL CHECK (n > 0),"
    " lo INT, hi INT, CHECK (lo <= hi));"
)


def test_array_child_schema() -> None:
    s = schema_of(EMB, "p", {"fk:c.p_id->p.id": "EMBED"})
    c = s["properties"]["c"]
    assert c["bsonType"] == "array" and c["items"]["bsonType"] == "object"
    items = c["items"]
    assert items["required"] == ["id", "n"] and "p_id" not in items["properties"]
    assert items["properties"]["n"]["minimum"] == 0 and items["additionalProperties"] is False
    assert "c" not in s.get("required", [])


def test_array_child_cross_column_check_iterates_elements() -> None:
    v = validator(EMB, "p", {"fk:c.p_id->p.id": "EMBED"})
    expr = v["$and"][1]["$expr"]
    assert "$allElementsTrue" in expr
    assert expr["$allElementsTrue"][0]["$map"]["input"] == {"$ifNull": ["$c", []]}
    assert expr["$allElementsTrue"][0]["$map"]["in"]["$or"][2] == {"$lte": ["$$e1.lo", "$$e1.hi"]}


def test_object_child_schema() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (p_id INT PRIMARY KEY REFERENCES p(id), bio TEXT NOT NULL);"
    c = schema_of(sql, "p", {"fk:c.p_id->p.id": "EMBED"})["properties"]["c"]
    assert c["bsonType"] == "object" and c["required"] == ["bio"] and "p_id" not in c["properties"]


def test_folded_junction_schemas() -> None:
    from schemashift.codegen.layout import build_layouts
    from schemashift.models import PlacementDecision, PlacementPlan

    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (a_id INT NOT NULL REFERENCES a(id), b_id INT NOT NULL REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
    )
    result, _ = compile_with_plan(sql)
    plan = PlacementPlan(
        decisions={
            "m2n:ab": PlacementDecision(relationship_id="m2n:ab", decision="REF_ARRAY", host="a")
        }
    )
    layouts = build_layouts(result.graph, plan)
    spec = next(c for c in build_collections(result.schema_, layouts).collections if c.table == "a")
    ids = spec.validator["$jsonSchema"]["properties"]["b_ids"]
    assert ids == {"bsonType": "array", "items": {"bsonType": "int"}, "uniqueItems": True}


def test_check_through_nested_object_and_array() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (p_id INT PRIMARY KEY REFERENCES p(id));"
        "CREATE TABLE g (id INT PRIMARY KEY, c_id INT NOT NULL REFERENCES c(p_id), x INT, y INT, CHECK (x < y));"
    )
    v = validator(sql, "p", {"fk:c.p_id->p.id": "EMBED", "fk:g.c_id->c.p_id": "EMBED"})
    expr = v["$and"][1]["$expr"]
    assert expr["$allElementsTrue"][0]["$map"]["input"] == {"$ifNull": ["$c.g", []]}


def test_check_referencing_implied_parent_key() -> None:
    sql = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), CHECK (id <> p_id));"
    )
    v = validator(sql, "p", {"fk:c.p_id->p.id": "EMBED"})
    inner = v["$and"][1]["$expr"]["$allElementsTrue"][0]["$map"]["in"]
    assert {"$ne": ["$$e1.id", "$_id"]} in inner["$or"]


# ----------------------------------------------------------------------- indexes
def by_origin(specs, origin):  # type: ignore[no-untyped-def]
    return [s for s in specs if s.origin == origin]


def test_unique_and_composite_pk_indexes() -> None:
    specs = indexes(
        "CREATE TABLE t (a INT, b INT, email TEXT NOT NULL UNIQUE, PRIMARY KEY (a, b), UNIQUE (a, email));"
    )
    pk = by_origin(specs, "pk:t.a,b")[0]
    assert pk.unique and pk.keys == (("a", 1), ("b", 1))
    assert by_origin(specs, "unique:t.email")[0].unique
    assert by_origin(specs, "unique:t.a,email")[0].partial_filter is None


def test_single_column_pk_has_no_index() -> None:
    assert indexes("CREATE TABLE t (id INT PRIMARY KEY);") == []


def test_nullable_unique_is_partial() -> None:
    spec = by_origin(
        indexes("CREATE TABLE t (id INT PRIMARY KEY, phone TEXT UNIQUE);"), "unique:t.phone"
    )[0]
    assert spec.unique and spec.partial_filter == {"phone": {"$exists": True}}


def test_composite_unique_partial_only_on_nullable_columns() -> None:
    spec = by_origin(
        indexes("CREATE TABLE t (id INT PRIMARY KEY, a INT NOT NULL, b INT, UNIQUE (a, b));"),
        "unique:t.a,b",
    )[0]
    assert spec.partial_filter == {"b": {"$exists": True}}


def test_partial_unique_index_from_sql_predicate() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, a TEXT NOT NULL, d BOOL NOT NULL); CREATE UNIQUE INDEX i ON t (a) WHERE d = true;"
    spec = by_origin(indexes(sql), "unique:t.a")[0]
    assert spec.unique and spec.partial_filter == {"d": True}


def test_untranslatable_partial_predicate_downgrades_to_plain_index() -> None:
    sql = "CREATE TABLE t (id INT PRIMARY KEY, a TEXT NOT NULL); CREATE UNIQUE INDEX i ON t (a) WHERE lower(a) = 'x';"
    spec = by_origin(indexes(sql), "unique:t.a")[0]
    assert not spec.unique and "NOT unique" in spec.comment


def test_unique_on_embedded_array_child_is_a_global_multikey_index() -> None:
    sql = EMB.replace("CHECK (lo <= hi)", "UNIQUE (n)")
    specs = indexes(sql, {"fk:c.p_id->p.id": "EMBED"})
    spec = by_origin(specs, "unique:c.n")[0]
    assert spec.collection == "p" and spec.keys == (("c.n", 1),) and spec.unique


def test_unique_per_parent_cannot_be_an_index() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT NOT NULL REFERENCES p(id), code TEXT NOT NULL, UNIQUE (p_id, code));"
    specs = indexes(sql, {"fk:c.p_id->p.id": "EMBED"})
    assert by_origin(specs, "unique:c.p_id,code") == []


def test_fk_equivalent_index_for_referenced_children() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id));"
    specs = indexes(sql)
    spec = by_origin(specs, "fk:c.p_id->p.id")[0]
    assert (spec.collection, spec.keys, spec.unique) == ("c", (("p_id", 1),), False)


def test_no_fk_index_for_embedded_relationship() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (id INT PRIMARY KEY, p_id INT REFERENCES p(id));"
    assert by_origin(indexes(sql, {"fk:c.p_id->p.id": "EMBED"}), "fk:c.p_id->p.id") == []


def test_fk_index_for_other_parent_inside_an_embedded_array() -> None:
    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT PRIMARY KEY, a_id INT REFERENCES a(id), b_id INT REFERENCES b(id));"
    )
    spec = by_origin(indexes(sql, {"fk:c.a_id->a.id": "EMBED"}), "fk:c.b_id->b.id")[0]
    assert spec.collection == "a" and spec.keys == (("c.b_id", 1),)


def test_no_fk_index_on_the_id_itself() -> None:
    sql = "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE c (p_id INT PRIMARY KEY REFERENCES p(id));"
    assert by_origin(indexes(sql), "fk:c.p_id->p.id") == []


def test_plain_sql_index_is_preserved() -> None:
    specs = indexes("CREATE TABLE t (id INT PRIMARY KEY, a INT); CREATE INDEX ix ON t (a);")
    assert [(s.name, s.keys, s.unique) for s in specs] == [("ix", (("a", 1),), False)]


def test_ref_array_gets_a_multikey_index() -> None:
    from schemashift.codegen.layout import build_layouts
    from schemashift.models import PlacementDecision, PlacementPlan

    sql = (
        "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);"
        "CREATE TABLE ab (a_id INT NOT NULL REFERENCES a(id), b_id INT NOT NULL REFERENCES b(id), PRIMARY KEY (a_id, b_id));"
    )
    result, _ = compile_with_plan(sql)
    plan = PlacementPlan(
        decisions={
            "m2n:ab": PlacementDecision(relationship_id="m2n:ab", decision="REF_ARRAY", host="a")
        }
    )
    specs = build_indexes(result.schema_, result.graph, build_layouts(result.graph, plan))
    assert [(s.collection, s.keys) for s in specs if s.origin == "m2n:ab"] == [
        ("a", (("b_ids", 1),))
    ]
    assert not [s for s in specs if s.origin.startswith("pk:ab")]  # folded: no PK index


def test_duplicate_indexes_are_collapsed() -> None:
    specs = indexes(
        "CREATE TABLE t (id INT PRIMARY KEY, a TEXT NOT NULL UNIQUE); CREATE UNIQUE INDEX u2 ON t (a);"
    )
    assert len([s for s in specs if s.keys == (("a", 1),) and s.unique]) == 1


def test_partial_filter_helper() -> None:
    result, layouts = compile_with_plan(
        "CREATE TABLE t (id INT PRIMARY KEY, a INT, d BOOL, s TEXT);"
    )
    t, lay = result.schema_.tables["t"], layouts["t"]
    assert partial_filter("a > 5 AND d", t, lay) == {"a": {"$gt": 5}, "d": True}
    assert partial_filter("a > 1 AND a < 9", t, lay) == {"a": {"$gt": 1, "$lt": 9}}
    assert partial_filter("s IS NOT NULL", t, lay) == {"s": {"$exists": True}}
    assert partial_filter("a > 5 OR d", t, lay) is None
    assert partial_filter("zzz = 1", t, lay) is None
    assert partial_filter("a = NULL", t, lay) is None
