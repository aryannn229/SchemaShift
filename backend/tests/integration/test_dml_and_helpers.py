"""DML translation, migration, validators and enforcement helpers against real databases."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import WriteError

from schemashift.codegen.helpers import generate_helpers
from schemashift.codegen.layout import build_layouts
from schemashift.codegen.migration import migration_plan
from schemashift.codegen.queries import QueryContext, translate_query
from schemashift.codegen.runtime_migrate import migrate
from schemashift.codegen.values import materialize
from schemashift.models import PlacementDecision, PlacementPlan
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions, compile_sql
from tests.integration.harness import run_select, setup_and_migrate
from tests.integration.test_translation_semantics import DATA, PLANS, SCHEMA

pytestmark = pytest.mark.integration

DML = """
INSERT INTO customers (id, name, tier) VALUES (6, 'fay', 'gold');
INSERT INTO orders (id, customer_id, total, placed) VALUES (15, 6, 9.99, '2024-05-05 00:00:00+00');
INSERT INTO items (id, order_id, sku, qty) VALUES (104, 15, 'z', 3);
UPDATE customers SET tier = 'plat' WHERE id = 2;
UPDATE orders SET total = 77.5, note = 'bulk' WHERE customer_id = 1;
UPDATE items SET qty = 9 WHERE order_id = 10 AND sku = 'a';
DELETE FROM items WHERE id = 101;
DELETE FROM profiles WHERE customer_id = 3;
UPDATE customers SET tier = NULL WHERE id = 1;
INSERT INTO tags (id, label) VALUES (3, 'sale');
INSERT INTO customer_tags (customer_id, tag_id) VALUES (2, 3);
DELETE FROM customer_tags WHERE customer_id = 1 AND tag_id = 2;
"""
TABLES = {
    "customers": "SELECT * FROM customers ORDER BY id",
    "orders": "SELECT * FROM orders ORDER BY id",
    "items": "SELECT * FROM items ORDER BY id",
    "profiles": "SELECT * FROM profiles ORDER BY customer_id",
    "tags": "SELECT * FROM tags ORDER BY id",
    "customer_tags": "SELECT * FROM customer_tags ORDER BY customer_id, tag_id",
}


@pytest.mark.parametrize("plan_name", list(PLANS))
def test_dml_has_the_same_effect_on_both_databases(
    plan_name: str, pg_schema: Any, mongo_db: Any
) -> None:
    conn, schema_name = pg_schema
    result, _ = setup_and_migrate(SCHEMA, DATA, "", PLANS[plan_name], conn, schema_name, mongo_db)
    layouts = build_layouts(result.graph, PLANS[plan_name])
    ctx = QueryContext(result.schema_, result.graph, layouts)
    for stmt in parse(DML, "queries").all_queries():
        tq = translate_query(stmt, ctx)
        assert tq.error is None, f"{stmt.raw_sql}: {tq.error}"
        conn.execute(stmt.raw_sql)
        for op in tq.operations:
            coll = mongo_db[op.collection]
            if op.op in ("insertOne", "insertMany"):
                docs = materialize(op.documents)
                coll.insert_many(docs) if op.op == "insertMany" else coll.insert_one(docs[0])
            elif op.op == "deleteMany":
                coll.delete_many(materialize(op.filter or {}))
            else:
                kwargs = (
                    {"array_filters": materialize(op.array_filters)} if op.array_filters else {}
                )
                method = coll.update_one if op.op == "updateOne" else coll.update_many
                res = method(materialize(op.filter or {}), materialize(op.update), **kwargs)
                assert res.matched_count >= 1 or op.op == "updateMany", (
                    f"{stmt.raw_sql}: matched nothing"
                )
    mismatches = []
    for table, sql in TABLES.items():
        select = parse(sql + ";", "queries").all_queries()[0]
        tq = translate_query(select, ctx)
        assert tq.error is None, tq.error
        out = run_select(conn, mongo_db, tq)
        if not out.ok:
            mismatches.append(f"{table}\n  PG   : {out.pg}\n  MONGO: {out.mongo}")
    assert not mismatches, "\n".join(mismatches)


def test_migration_is_idempotent_and_shapes_documents(pg_schema: Any, mongo_db: Any) -> None:
    conn, schema_name = pg_schema
    plan = PLANS["items+profiles-embedded"]
    result, _ = setup_and_migrate(SCHEMA, DATA, "", plan, conn, schema_name, mongo_db)
    order10 = mongo_db["orders"].find_one({"_id": 10})
    assert order10["items"] == [
        {"id": 100, "sku": "a", "qty": 2},
        {"id": 101, "sku": "b", "qty": 1},
    ]
    assert "note" not in mongo_db["orders"].find_one({"_id": 11})  # NULL = absent field
    assert mongo_db["customers"].find_one({"_id": 1})["profiles"] == {"bio": "hello"}
    assert "profiles" not in mongo_db["customers"].find_one({"_id": 2})
    assert mongo_db["customers"].find_one({"_id": 3})["profiles"] == {}  # row exists, bio NULL
    layouts = build_layouts(result.graph, plan)
    counts = migrate(
        conn,
        mongo_db,
        migration_plan(result.schema_, layouts, CompileOptions()),
        schema=schema_name,
        log=lambda *_: None,
    )
    assert counts == {"customers": 5, "orders": 5, "tags": 2, "customer_tags": 3}
    assert mongo_db["orders"].count_documents({}) == 5  # upserts, no duplicates


def test_migration_with_composite_keys_uses_deterministic_ids(
    pg_schema: Any, mongo_db: Any
) -> None:
    conn, schema_name = pg_schema
    conn.execute(
        "CREATE TABLE t (a INT, b INT, v TEXT, PRIMARY KEY (a, b)); INSERT INTO t VALUES (1,1,'x'),(1,2,'y');"
    )
    result = compile_sql("CREATE TABLE t (a INT, b INT, v TEXT, PRIMARY KEY (a, b));")
    plan = migration_plan(
        result.schema_, build_layouts(result.graph, PlacementPlan()), CompileOptions()
    )
    migrate(conn, mongo_db, plan, schema=schema_name, log=lambda *_: None)
    first = sorted(d["_id"] for d in mongo_db["t"].find())
    migrate(conn, mongo_db, plan, schema=schema_name, log=lambda *_: None)
    assert sorted(d["_id"] for d in mongo_db["t"].find()) == first and len(first) == 2


def test_migration_type_conversions(pg_schema: Any, mongo_db: Any) -> None:
    from bson import Decimal128

    ddl = (
        "CREATE TABLE t (id INT PRIMARY KEY, d NUMERIC(10,2), ts TIMESTAMPTZ, dt DATE, u UUID, j JSONB, "
        "arr INT[], tm TIME, f DOUBLE PRECISION, bi BIGINT, bo BOOLEAN, by BYTEA, iv INTERVAL);"
    )
    conn, schema_name = pg_schema
    conn.execute(ddl)
    conn.execute(
        "INSERT INTO t VALUES (1, 12.50, '2024-01-01 12:00:00+02', '2024-03-04', "
        "'123e4567-e89b-12d3-a456-426614174000', '{\"a\": [1, 2]}', '{1,2,3}', '10:30:00', 1.5, 9000000000, true, "
        "'\\x0102', '1 day 2 hours')"
    )
    result = compile_sql(ddl)
    plan = migration_plan(
        result.schema_, build_layouts(result.graph, PlacementPlan()), CompileOptions()
    )
    migrate(conn, mongo_db, plan, schema=schema_name, log=lambda *_: None)
    doc = mongo_db["t"].find_one({"_id": 1})
    assert doc["d"] == Decimal128("12.50")
    assert doc["ts"].isoformat().startswith("2024-01-01T10:00:00")
    assert doc["dt"].isoformat().startswith("2024-03-04T00:00:00")
    assert doc["j"] == {"a": [1, 2]} and doc["arr"] == [1, 2, 3] and doc["tm"] == "10:30:00"
    assert (
        doc["f"] == 1.5
        and doc["bi"] == 9000000000
        and doc["bo"] is True
        and doc["by"] == b"\x01\x02"
    )
    assert doc["iv"].startswith("PT") and doc["u"].subtype == 4


def test_validators_accept_valid_and_reject_invalid_documents(mongo_db: Any) -> None:
    from schemashift.codegen.collections import build_collections

    sql = (
        "CREATE TABLE acct (id INT PRIMARY KEY, email TEXT NOT NULL UNIQUE, balance NUMERIC(10,2) NOT NULL DEFAULT 0 "
        "CHECK (balance >= 0), kind TEXT NOT NULL CHECK (kind IN ('a', 'b')), lo INT, hi INT, CHECK (lo <= hi), "
        "nick VARCHAR(5), phone TEXT UNIQUE);"
    )
    result = compile_sql(sql)
    layouts = build_layouts(result.graph, PlacementPlan())
    from bson import Decimal128

    for spec in build_collections(result.schema_, layouts).collections:
        mongo_db.create_collection(
            spec.name,
            validator=materialize(spec.validator),
            validationLevel="strict",
            validationAction="error",
        )
    from schemashift.codegen.indexes import build_indexes

    for ix in build_indexes(result.schema_, result.graph, layouts):
        kw: dict[str, Any] = {"name": ix.name, "unique": ix.unique}
        if ix.partial_filter:
            kw["partialFilterExpression"] = ix.partial_filter
        mongo_db[ix.collection].create_index(list(ix.keys), **kw)
    good = {"_id": 1, "email": "a@x", "balance": Decimal128("5"), "kind": "a", "lo": 1, "hi": 2}
    mongo_db.acct.insert_one(good)
    bad_docs = [
        {**good, "_id": 2, "email": "b@x", "balance": Decimal128("-1")},  # CHECK (balance >= 0)
        {**good, "_id": 3, "email": "c@x", "kind": "z"},  # CHECK IN list
        {**good, "_id": 4, "email": "d@x", "lo": 9, "hi": 1},  # cross-column CHECK ($expr)
        {**good, "_id": 5, "email": "e@x", "nick": "toolong"},  # VARCHAR(5)
        {"_id": 6, "email": "f@x", "balance": Decimal128("1")},  # missing required kind
        {**good, "_id": 7, "email": "g@x", "unknown": 1},  # additionalProperties
        {**good, "_id": 8, "email": 5},  # wrong bsonType
        {**good, "_id": 9, "email": "h@x", "lo": None},  # NULL must be absent, not null
    ]
    for doc in bad_docs:
        with pytest.raises(WriteError):
            mongo_db.acct.insert_one(doc)
    # NULL (absent) passes the CHECKs, and two NULL phones do not collide in the partial unique index
    mongo_db.acct.insert_one({"_id": 10, "email": "i@x", "balance": Decimal128("0"), "kind": "b"})
    mongo_db.acct.insert_one({"_id": 11, "email": "j@x", "balance": Decimal128("0"), "kind": "b"})
    mongo_db.acct.insert_one(
        {"_id": 12, "email": "k@x", "balance": Decimal128("0"), "kind": "b", "phone": "1"}
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 (DuplicateKeyError)
        mongo_db.acct.insert_one(
            {"_id": 13, "email": "l@x", "balance": Decimal128("0"), "kind": "b", "phone": "1"}
        )
    with pytest.raises(Exception):  # noqa: B017, PT011
        mongo_db.acct.insert_one(
            {"_id": 14, "email": "a@x", "balance": Decimal128("0"), "kind": "b"}
        )


# ----------------------------------------------------------------------------- helpers
HELPER_SCHEMA = """
CREATE TABLE accounts (id INT PRIMARY KEY, name TEXT NOT NULL UNIQUE, status TEXT DEFAULT 'open');
CREATE TABLE txns (id INT PRIMARY KEY, account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, amount INT NOT NULL);
CREATE TABLE refunds (id INT PRIMARY KEY, txn_id INT NOT NULL REFERENCES txns(id) ON DELETE CASCADE);
CREATE TABLE notes (id INT PRIMARY KEY, account_id INT REFERENCES accounts(id) ON DELETE SET NULL, body TEXT);
CREATE TABLE cards (id INT PRIMARY KEY, account_id INT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT, num TEXT NOT NULL UNIQUE);
CREATE TABLE codes (code TEXT PRIMARY KEY, label TEXT);
CREATE TABLE tickets (id INT PRIMARY KEY, code TEXT REFERENCES codes(code) ON UPDATE CASCADE ON DELETE CASCADE);
"""
HELPER_DATA = """
INSERT INTO accounts VALUES (1,'ann','open'),(2,'bob','open'),(3,'cy','open');
INSERT INTO txns VALUES (10,1,100),(11,1,50),(12,2,70);
INSERT INTO refunds VALUES (100,10),(101,12);
INSERT INTO notes VALUES (1,1,'n1'),(2,1,'n2'),(3,2,'n3');
INSERT INTO cards VALUES (1,2,'4111'),(2,3,'4222');
INSERT INTO codes VALUES ('A','alpha'),('B','beta');
INSERT INTO tickets VALUES (1,'A'),(2,'A'),(3,'B');
"""
HELPER_PLANS = {
    "referenced": PlacementPlan(),
    "txns+refunds-embedded": PlacementPlan.of(
        {"fk:txns.account_id->accounts.id": "EMBED", "fk:refunds.txn_id->txns.id": "EMBED"}
    ),
    "notes+cards-embedded": PlacementPlan.of(
        {"fk:notes.account_id->accounts.id": "EMBED", "fk:cards.account_id->accounts.id": "EMBED"}
    ),
}
HELPER_TABLES = ["accounts", "txns", "refunds", "notes", "cards", "codes", "tickets"]


def load_helpers(result: Any, layouts: Any) -> dict[str, Any]:
    from schemashift.codegen import make_header

    gen = generate_helpers(
        result, layouts, CompileOptions(), make_header("it", "2026-01-01T00:00:00Z"), []
    )
    # Run the generated per-table wrappers against the real runtime module (so it is measured);
    # a unit test asserts that the generated file embeds that module's source verbatim.
    import ast

    import schemashift.codegen.runtime_helpers as runtime

    text = gen.content
    start = text.index("PLAN = ") + len("PLAN = ")
    ns: dict[str, Any] = dict(vars(runtime))
    ns["PLAN"] = ast.literal_eval(text[start : text.index("\n\n\n# --- Runtime")])
    wrappers = text[text.index("# ---- per-table helpers (generated)") :]
    exec(compile(wrappers, "<helpers>", "exec"), ns)  # noqa: S102 (our own generated code)
    return ns


def snapshot(
    conn: Any, mongo_db: Any, ctx: QueryContext, ignore: set[str] | None = None
) -> list[str]:
    bad = []
    for t in HELPER_TABLES:
        if ignore and t in ignore:
            continue
        select = parse(f"SELECT * FROM {t};", "queries").all_queries()[0]
        tq = translate_query(select, ctx)
        assert tq.error is None, tq.error
        out = run_select(conn, mongo_db, tq)
        if not out.ok:
            bad.append(f"{t}: PG {sorted(map(str, out.pg))} != MONGO {sorted(map(str, out.mongo))}")
    return bad


@pytest.fixture
def helper_env(request: Any, pg_schema: Any, mongo_db: Any, mongo_client: Any) -> Any:
    plan = HELPER_PLANS[request.param]
    conn, schema_name = pg_schema
    result, _ = setup_and_migrate(HELPER_SCHEMA, HELPER_DATA, "", plan, conn, schema_name, mongo_db)
    layouts = build_layouts(result.graph, plan)
    ctx = QueryContext(result.schema_, result.graph, layouts)
    return conn, mongo_db, mongo_client, load_helpers(result, layouts), ctx


@pytest.mark.parametrize("helper_env", list(HELPER_PLANS), indirect=True)
def test_cascade_set_null_chain_matches_postgres(helper_env: Any, request: Any) -> None:
    conn, db, client, h, ctx = helper_env
    # account 1: cascades txns 10, 11 and refund 100, nulls notes 1, 2; no cards -> allowed
    conn.execute("DELETE FROM accounts WHERE id = 1")
    h["delete_accounts"](client, db, id=1)
    # SET NULL on an *embedded* child cannot keep the row: it is deleted with its parent
    # (verdict EQ-SET-NULL/EMBEDDED = CHANGED), so `notes` legitimately differs in that layout.
    embedded_notes = request.node.callspec.id == "notes+cards-embedded"
    assert snapshot(conn, db, ctx, ignore={"notes"} if embedded_notes else None) == []
    if embedded_notes:
        assert db["accounts"].count_documents({"_id": 1}) == 0


@pytest.mark.parametrize("helper_env", list(HELPER_PLANS), indirect=True)
def test_restrict_blocks_delete_and_changes_nothing(helper_env: Any) -> None:
    conn, db, client, h, ctx = helper_env
    with pytest.raises(h["IntegrityError"]):
        h["delete_accounts"](client, db, id=2)  # account 2 has a card (RESTRICT)
    with pytest.raises(Exception):  # noqa: B017, PT011 (PostgreSQL agrees)
        conn.execute("DELETE FROM accounts WHERE id = 2")
    assert snapshot(conn, db, ctx) == []


@pytest.mark.parametrize("helper_env", list(HELPER_PLANS), indirect=True)
def test_insert_checks_foreign_keys_defaults_and_uniqueness(helper_env: Any) -> None:
    conn, db, client, h, ctx = helper_env
    with pytest.raises(h["IntegrityError"]):
        h["insert_txns"](db, {"id": 50, "account_id": 999, "amount": 1})
    h["insert_accounts"](db, {"id": 4, "name": "dan"})  # status default 'open' filled in
    conn.execute("INSERT INTO accounts (id, name) VALUES (4, 'dan')")
    h["insert_txns"](db, {"id": 51, "account_id": 4, "amount": 5})
    conn.execute("INSERT INTO txns VALUES (51, 4, 5)")
    h["insert_refunds"](db, {"id": 102, "txn_id": 51})
    conn.execute("INSERT INTO refunds VALUES (102, 51)")
    with pytest.raises(h["IntegrityError"]):
        h["insert_refunds"](db, {"id": 103, "txn_id": 4040})
    with pytest.raises((h["IntegrityError"], Exception)):
        h["insert_cards"](db, {"id": 9, "account_id": 4, "num": "4111"})  # duplicate unique num
    assert snapshot(conn, db, ctx) == []


@pytest.mark.parametrize("helper_env", list(HELPER_PLANS), indirect=True)
def test_update_cascade_of_a_natural_key(helper_env: Any) -> None:
    conn, db, client, h, ctx = helper_env
    conn.execute("UPDATE codes SET code = 'Z' WHERE code = 'A'")
    h["update_codes"](client, db, {"code": "A"}, {"code": "Z"})
    assert snapshot(conn, db, ctx) == []
    assert sorted(d["code"] for d in db["tickets"].find({"code": "Z"})) == ["Z", "Z"]


def test_counters_replace_serial_when_integer_ids_are_preserved(mongo_db: Any) -> None:
    from schemashift.codegen.runtime_helpers import next_id

    assert [next_id(mongo_db, "t.id") for _ in range(3)] == [1, 2, 3]
    assert next_id(mongo_db, "other.id") == 1


def test_junction_helper_adds_and_removes_references(
    pg_schema: Any, mongo_db: Any, mongo_client: Any
) -> None:
    conn, schema_name = pg_schema
    plan = PLANS["junction-folded"]
    result, _ = setup_and_migrate(SCHEMA, DATA, "", plan, conn, schema_name, mongo_db)
    layouts = build_layouts(result.graph, plan)
    h = load_helpers(result, layouts)
    h["insert_customer_tags"](mongo_db, {"customer_id": 2, "tag_id": 2})
    assert mongo_db["customers"].find_one({"_id": 2})["tag_ids"] == [2]
    with pytest.raises(h["IntegrityError"]):
        h["insert_customer_tags"](mongo_db, {"customer_id": 2, "tag_id": 2})  # duplicate pair
    with pytest.raises(h["IntegrityError"]):
        h["insert_customer_tags"](mongo_db, {"customer_id": 2, "tag_id": 77})  # missing tag
    h["delete_customer_tags"](mongo_client, mongo_db, customer_id=2, tag_id=2)
    assert mongo_db["customers"].find_one({"_id": 2}).get("tag_ids", []) == []


_ = PlacementDecision


def test_generated_migrate_script_runs_as_a_standalone_program(
    pg_schema: Any, mongo_db: Any, tmp_path: Any
) -> None:
    import subprocess
    import sys

    from schemashift.codegen import generate_code

    conn, schema_name = pg_schema
    conn.execute(SCHEMA)
    conn.execute(DATA)
    opts = CompileOptions()
    result = compile_sql(SCHEMA, "", "", opts)
    script = tmp_path / "migrate.py"
    script.write_text(
        generate_code(result, opts, run_id="e2e", timestamp="t").file("python/migrate.py").content,
        encoding="utf-8",
    )
    from tests.conftest import MONGO_URL, PG_DSN

    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source",
            PG_DSN,
            "--target",
            MONGO_URL,
            "--database",
            mongo_db.name,
            "--schema",
            schema_name,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "done:" in proc.stdout and "customers=5" in proc.stdout
    assert mongo_db["customers"].count_documents({}) == 5
