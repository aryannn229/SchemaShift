from typing import Any

import pytest

from schemashift.codegen.values import DateValue, DecimalValue, NowValue
from tests.unit.codegen.helpers import translate

SCHEMA = """
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL, tier TEXT);
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id),
  total NUMERIC(10,2) NOT NULL, note TEXT, placed TIMESTAMPTZ DEFAULT now(), status TEXT DEFAULT 'new');
CREATE TABLE items (id INT PRIMARY KEY, order_id INT NOT NULL REFERENCES orders(id), sku TEXT NOT NULL, qty INT NOT NULL);
CREATE TABLE profiles (customer_id INT PRIMARY KEY REFERENCES customers(id), bio TEXT);
CREATE TABLE tags (id INT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE customer_tags (customer_id INT NOT NULL REFERENCES customers(id), tag_id INT NOT NULL REFERENCES tags(id), PRIMARY KEY (customer_id, tag_id));
"""
REF: dict[str, Any] = {}
EMB: dict[str, Any] = {
    "fk:items.order_id->orders.id": "EMBED",
    "fk:profiles.customer_id->customers.id": "EMBED",
}


def one(queries: str, plan: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    out = translate(SCHEMA, queries, plan)
    assert len(out) == 1
    return out[0]


def stages(queries: str, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    q = one(queries, plan)
    assert q.error is None, q.error
    assert q.pipeline is not None
    return q.pipeline


def ops(queries: str, plan: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    q = one(queries, plan)
    assert q.error is None, q.error
    return q.operations


def names(pipeline: list[dict[str, Any]]) -> list[str]:
    return [next(iter(s)) for s in pipeline]


# --------------------------------------------------------------------- basics
def test_simple_select_projects_with_null_for_missing() -> None:
    p = stages("SELECT id, name FROM customers;")
    assert names(p) == ["$replaceRoot", "$project"]
    assert p[1]["$project"] == {
        "_id": 0,
        "id": {"$ifNull": ["$customers._id", None]},
        "name": {"$ifNull": ["$customers.name", None]},
    }
    assert one("SELECT id, name FROM customers;").output_columns == ["id", "name"]
    assert one("SELECT id FROM customers;").collection == "customers"


def test_select_star_expands_in_schema_order() -> None:
    assert one("SELECT * FROM customers;").output_columns == ["id", "name", "tier"]
    assert one("SELECT c.* FROM customers c;").output_columns == ["id", "name", "tier"]


def test_aliases_and_default_names() -> None:
    q = one("SELECT name AS n, id, COUNT(*), id + 1 FROM customers GROUP BY name, id;")
    assert q.output_columns == ["n", "id", "count", "column4"]


def test_duplicate_output_names_are_suffixed() -> None:
    assert one(
        "SELECT c.id, o.id FROM customers c JOIN orders o ON o.customer_id = c.id;"
    ).output_columns == ["id", "id_2"]


def test_where_is_pushed_down_before_replace_root() -> None:
    p = stages("SELECT id FROM customers WHERE tier = 'gold' AND id > 5;")
    assert names(p)[:2] == ["$match", "$replaceRoot"]
    assert p[0]["$match"] == {"$and": [{"tier": "gold"}, {"_id": {"$gt": 5}}]}


def test_where_on_joined_table_is_placed_after_the_join() -> None:
    p = stages(
        "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id WHERE o.total > 5 AND c.tier = 'x';"
    )
    assert p[0] == {"$match": {"tier": "x"}}  # base-table term pushed down
    kinds = names(p)
    assert kinds.index("$match", 1) > kinds.index("$unwind")  # joined-table term after the join


def test_null_semantics_in_where() -> None:
    p = stages("SELECT id FROM customers WHERE tier <> 'a' AND tier IS NOT NULL;")
    assert p[0]["$match"]["$and"][0] == {"tier": {"$nin": ["a", None]}}


def test_limit_offset_order() -> None:
    p = stages("SELECT id FROM customers ORDER BY id LIMIT 5 OFFSET 2;")
    kinds = names(p)
    assert kinds[-4:] == ["$skip", "$limit", "$project"] or kinds[-3:] == [
        "$skip",
        "$limit",
        "$project",
    ]
    assert {"$skip": 2} in p and {"$limit": 5} in p
    assert p.index({"$skip": 2}) < p.index({"$limit": 5})


def test_order_by_emulates_postgres_null_ordering() -> None:
    p = stages("SELECT id FROM customers ORDER BY tier, id DESC;")
    add = next(s for s in p if "$addFields" in s)["$addFields"]
    sort = next(s for s in p if "$sort" in s)["$sort"]
    assert add["_n0"] == {"$cond": [{"$lte": ["$customers.tier", None]}, 1, 0]}
    assert sort["_n0"] == 1  # ASC: NULLs last
    assert sort["_n1"] == -1  # DESC: NULLs first


def test_order_by_nulls_first_last_clauses() -> None:
    p = stages("SELECT id FROM customers ORDER BY tier DESC NULLS LAST;")
    assert next(s for s in p if "$sort" in s)["$sort"]["_n0"] == 1


def test_order_by_alias_ordinal_and_expression() -> None:
    p = stages("SELECT name AS n FROM customers ORDER BY n;")
    assert "n" in next(s for s in p if "$sort" in s)["$sort"]
    p = stages("SELECT name FROM customers ORDER BY 1;")
    assert "name" in next(s for s in p if "$sort" in s)["$sort"]
    p = stages("SELECT name FROM customers ORDER BY id + 1;")
    assert "_k0" in next(s for s in p if "$sort" in s)["$sort"]


def test_distinct() -> None:
    p = stages("SELECT DISTINCT tier FROM customers;")
    assert "$group" in names(p) and "$replaceRoot" in names(p)
    assert p[-1] == {"$project": {"_id": 0, "tier": 1}}


# ------------------------------------------------------------------- aggregation
def test_group_by_with_aggregates_and_having() -> None:
    p = stages(
        "SELECT customer_id, COUNT(*) AS n, SUM(total) AS s, AVG(total) AS a, MIN(total), MAX(total) "
        "FROM orders GROUP BY customer_id HAVING COUNT(*) > 1 ORDER BY n DESC;"
    )
    group = next(s for s in p if "$group" in s)["$group"]
    assert group["_id"] == {"g0": "$orders.customer_id"}
    assert group["__a0"] == {"$sum": 1}
    assert group["__a1"] == {"$sum": "$orders.total"}
    assert group["__a1_n"] == {"$sum": {"$cond": [{"$gt": ["$orders.total", None]}, 1, 0]}}
    assert group["__a2"] == {"$avg": "$orders.total"}
    add = next(s for s in p if "$addFields" in s)["$addFields"]
    assert add["s"] == {
        "$ifNull": [{"$cond": [{"$eq": ["$__a1_n", 0]}, None, "$__a1"]}, None]
    }  # SUM of nothing = NULL
    match = next(s for s in p if "$match" in s)["$match"]["$expr"]
    assert match == {"$and": [{"$gt": ["$__a0", None]}, {"$gt": ["$__a0", 1]}]}
    assert p[-1] == {
        "$project": {"_id": 0, "customer_id": 1, "n": 1, "s": 1, "a": 1, "min": 1, "max": 1}
    }


def test_count_column_skips_nulls() -> None:
    p = stages("SELECT COUNT(note) FROM orders;")
    group = next(s for s in p if "$group" in s)["$group"]
    assert group["__a0"] == {"$sum": {"$cond": [{"$gt": ["$orders.note", None]}, 1, 0]}}
    assert group["_id"] is None


def test_count_distinct() -> None:
    p = stages("SELECT COUNT(DISTINCT customer_id) AS c FROM orders;")
    group = next(s for s in p if "$group" in s)["$group"]
    assert "$addToSet" in group["__a0"]
    assert next(s for s in p if "$addFields" in s)["$addFields"]["c"] == {
        "$ifNull": [{"$size": "$__a0"}, None]
    }


def test_arithmetic_over_aggregates() -> None:
    p = stages("SELECT SUM(total) / COUNT(*) AS avg_total FROM orders;")
    add = next(s for s in p if "$addFields" in s)["$addFields"]["avg_total"]["$ifNull"][0]
    assert "$divide" in add


def test_group_by_position_and_alias() -> None:
    a = stages("SELECT customer_id AS cid, COUNT(*) FROM orders GROUP BY 1;")
    b = stages("SELECT customer_id AS cid, COUNT(*) FROM orders GROUP BY cid;")
    assert (
        next(s for s in a if "$group" in s)["$group"]["_id"]
        == next(s for s in b if "$group" in s)["$group"]["_id"]
    )


def test_ungrouped_column_is_rejected() -> None:
    q = one("SELECT customer_id, COUNT(*) FROM orders;")
    assert q.error and "GROUP BY" in q.error


def test_order_by_aggregate_not_in_select() -> None:
    p = stages("SELECT customer_id FROM orders GROUP BY customer_id ORDER BY SUM(total) DESC;")
    assert any("_k0" in s.get("$sort", {}) for s in p)


# ------------------------------------------------------------------------ joins
def test_referenced_inner_join_uses_lookup_and_unwind() -> None:
    p = stages(
        "SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id;", REF
    )
    assert p[1] == {
        "$lookup": {
            "from": "orders",
            "localField": "c._id",
            "foreignField": "customer_id",
            "as": "o",
        }
    }
    assert p[2] == {"$unwind": {"path": "$o", "preserveNullAndEmptyArrays": False}}


def test_left_join_preserves_unmatched() -> None:
    p = stages("SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id;", REF)
    assert p[2]["$unwind"]["preserveNullAndEmptyArrays"] is True


def test_nullable_join_column_uses_pipeline_lookup() -> None:
    p = stages("SELECT c.id FROM customers c JOIN orders o ON o.note = c.tier;", REF)
    lookup = p[1]["$lookup"]
    assert (
        "pipeline" in lookup
        and {"$gt": ["$$v0", None]} in lookup["pipeline"][0]["$match"]["$expr"]["$and"]
    )


def test_multiple_equalities_use_pipeline_lookup() -> None:
    p = stages(
        "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id AND o.note = c.name;",
        REF,
    )
    assert len(p[1]["$lookup"]["let"]) == 2


def test_join_across_embedded_array_unwinds() -> None:
    p = stages("SELECT o.id, i.sku FROM orders o JOIN items i ON i.order_id = o.id;", EMB)
    assert p[1] == {"$unwind": {"path": "$o.items", "preserveNullAndEmptyArrays": False}}
    proj = p[-1]["$project"]
    assert proj["sku"] == {"$ifNull": ["$o.items.sku", None]} and proj["id"] == {
        "$ifNull": ["$o._id", None]
    }


def test_left_join_across_embedded_array_preserves_empty() -> None:
    p = stages("SELECT o.id FROM orders o LEFT JOIN items i ON i.order_id = o.id;", EMB)
    assert p[1]["$unwind"]["preserveNullAndEmptyArrays"] is True


def test_embedded_one_to_one_inner_join_requires_the_object() -> None:
    p = stages(
        "SELECT c.name, p.bio FROM customers c JOIN profiles p ON p.customer_id = c.id;", EMB
    )
    assert p[1] == {"$match": {"c.profiles": {"$exists": True}}}


def test_from_embedded_table_starts_at_the_host_collection() -> None:
    q = one("SELECT sku, qty FROM items WHERE qty > 2;", EMB)
    assert q.collection == "orders" and q.error is None
    assert {
        "$unwind": {"path": "$_orders.items", "preserveNullAndEmptyArrays": False}
    } in q.pipeline  # type: ignore[operator]


def test_from_embedded_joining_its_host() -> None:
    q = one("SELECT i.sku, o.total FROM items i JOIN orders o ON i.order_id = o.id;", EMB)
    assert q.error is None and q.collection == "orders"
    proj = q.pipeline[-1]["$project"]  # type: ignore[index]
    assert proj["total"] == {"$ifNull": ["$_orders.total", None]}


def test_join_must_use_the_embedding_foreign_key() -> None:
    q = one("SELECT o.id FROM orders o JOIN items i ON i.sku = o.note;", EMB)
    assert q.error and "foreign key" in q.error


def test_embedded_join_target_whose_host_is_not_in_scope_looks_up_the_host() -> None:
    p = stages("SELECT c.id, i.sku FROM customers c JOIN items i ON i.order_id = c.id;", EMB)
    assert p[1]["$lookup"]["from"] == "orders" and p[1]["$lookup"]["foreignField"] == "_id"
    assert any(s.get("$unwind", {}).get("path") == "$i__host.items" for s in p)


def test_unknown_names_are_reported() -> None:
    assert "unknown table" in (one("SELECT * FROM ghosts;").error or "")
    assert "unknown column" in (one("SELECT nope FROM customers;").error or "")
    assert "ambiguous" in (
        one("SELECT id FROM customers c JOIN orders o ON o.customer_id = c.id;").error or ""
    )
    assert "alias" in (one("SELECT x.id FROM customers c;").error or "")


def test_select_without_from_is_not_supported() -> None:
    assert one("SELECT 1;").error


# ---------------------------------------------------------------------------- DML
def test_insert_root_uses_column_types_and_defaults() -> None:
    (op,) = ops("INSERT INTO orders (id, customer_id, total) VALUES (1, 2, 9.5);")
    assert op.op == "insertOne" and op.collection == "orders"
    doc = op.documents[0]  # type: ignore[index]
    assert doc["_id"] == 1 and doc["customer_id"] == 2
    assert (
        doc["total"] == DecimalValue("9.5")
        and isinstance(doc["placed"], NowValue)
        and doc["status"] == "new"
    )
    assert "note" not in doc  # NULL = absent


def test_insert_many_rows() -> None:
    (op,) = ops("INSERT INTO customers (id, name) VALUES (1, 'a'), (2, 'b');")
    assert op.op == "insertMany" and len(op.documents) == 2  # type: ignore[arg-type]


def test_insert_null_value_is_omitted() -> None:
    (op,) = ops("INSERT INTO customers (id, name, tier) VALUES (1, 'a', NULL);")
    assert "tier" not in op.documents[0]  # type: ignore[index]


def test_insert_into_embedded_array_pushes() -> None:
    (op,) = ops("INSERT INTO items (id, order_id, sku, qty) VALUES (1, 7, 'x', 2);", EMB)
    assert op.op == "updateOne" and op.collection == "orders" and op.filter == {"_id": 7}
    assert op.update == {"$push": {"items": {"id": 1, "sku": "x", "qty": 2}}}


def test_insert_into_embedded_object_sets() -> None:
    (op,) = ops("INSERT INTO profiles (customer_id, bio) VALUES (3, 'hi');", EMB)
    assert op.filter == {"_id": 3} and op.update == {"$set": {"profiles": {"bio": "hi"}}}


def test_insert_into_embedded_requires_the_parent_key() -> None:
    q = one("INSERT INTO items (id, sku, qty) VALUES (1, 'x', 2);", EMB)
    assert q.error and "order_id" in q.error


def test_insert_column_count_mismatch() -> None:
    assert one("INSERT INTO customers (id, name) VALUES (1);").error


def test_update_literal_uses_set_and_unset() -> None:
    (op,) = ops("UPDATE customers SET name = 'z', tier = NULL WHERE id = 1;")
    assert op.op == "updateMany" and op.filter == {"_id": 1}
    assert op.update == {"$set": {"name": "z"}, "$unset": {"tier": ""}}


def test_update_with_column_reference_uses_pipeline_update() -> None:
    (op,) = ops("UPDATE orders SET total = total * 2 WHERE id = 1;")
    assert op.update == [{"$set": {"total": {"$multiply": ["$total", 2]}}}]


def test_update_primary_key_is_rejected() -> None:
    assert "immutable" in (one("UPDATE customers SET id = 5 WHERE id = 1;").error or "")


def test_update_embedded_array_uses_array_filters() -> None:
    (op,) = ops("UPDATE items SET qty = 5 WHERE id = 3;", EMB)
    assert op.update == {"$set": {"items.$[el].qty": 5}} and op.array_filters == [{"el.id": 3}]


def test_update_embedded_with_parent_key_filters_the_host() -> None:
    (op,) = ops("UPDATE items SET qty = 5 WHERE order_id = 9 AND sku = 'x';", EMB)
    # host key + a narrowing hint for the index + a guard so positional updates have an array
    assert op.filter == {"_id": 9, "items.sku": "x", "items": {"$exists": True}}
    assert op.array_filters == [{"el.sku": "x"}]


def test_update_embedded_object() -> None:
    (op,) = ops("UPDATE profiles SET bio = 'b' WHERE customer_id = 1;", EMB)
    assert op.update == {"$set": {"profiles.bio": "b"}}
    assert op.filter == {"_id": 1, "profiles": {"$exists": True}}


def test_update_embedded_rejects_column_expressions() -> None:
    assert one("UPDATE items SET qty = qty + 1 WHERE id = 1;", EMB).error


def test_delete_root() -> None:
    q = one("DELETE FROM orders WHERE id = 4;")
    assert q.operations[0].op == "deleteMany" and q.operations[0].filter == {"_id": 4}
    assert any("does not cascade" in n for n in q.notes)


def test_delete_embedded_array_pulls() -> None:
    (op,) = ops("DELETE FROM items WHERE id = 2 AND order_id = 5;", EMB)
    assert (
        op.op == "updateMany"
        and op.filter == {"_id": 5}
        and op.update == {"$pull": {"items": {"_id": 2}}}
        or (op.update == {"$pull": {"items": {"id": 2}}})
    )


def test_delete_embedded_object_unsets() -> None:
    (op,) = ops("DELETE FROM profiles WHERE customer_id = 1;", EMB)
    assert op.update == {"$unset": {"profiles": ""}}


def test_dml_unknown_names() -> None:
    assert "unknown table" in (one("DELETE FROM ghosts WHERE id = 1;").error or "")
    assert "unknown column" in (one("INSERT INTO customers (id, zz) VALUES (1, 2);").error or "")


# ------------------------------------------------------------ transactions + literals
def test_transaction_statements_are_tagged() -> None:
    out = translate(
        SCHEMA,
        "BEGIN; DELETE FROM items WHERE id = 1; UPDATE orders SET note = 'x' WHERE id = 2; COMMIT;",
    )
    assert [q.transaction_id for q in out] == ["t1", "t1"]


def test_date_literals_follow_the_column_type() -> None:
    p = stages("SELECT id FROM orders WHERE placed >= '2024-05-01';")
    assert p[0]["$match"] == {"placed": {"$gte": DateValue("2024-05-01T00:00:00+00:00")}}


@pytest.mark.parametrize(
    "sql",
    ["SELECT id FROM customers WHERE UPPER(name) = 'A';"],
)
def test_untranslatable_where_reports_the_fragment(sql: str) -> None:
    # the SQL parser already rejects non-whitelisted functions, so this goes through compile only
    from schemashift.pipeline import compile_sql

    assert any(d.code == "UNSUPPORTED_FUNCTION_CALL" for d in compile_sql(SCHEMA, sql).diagnostics)
