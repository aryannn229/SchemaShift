"""Phase 22 acceptance: v1 migration + incremental v1->v2 migration == a fresh v2 migration, on real databases.

PostgreSQL really executes the ALTER statements, so its state after them is the ground truth; MongoDB A is
migrated from v1 and then evolved by the generated incremental script, MongoDB B is migrated from scratch
from the evolved PostgreSQL state. They must hold identical documents, indexes and validators. Rolling the
script back must restore the v1 documents.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from bson import Decimal128

from schemashift.codegen.collections import build_collections
from schemashift.codegen.indexes import build_indexes
from schemashift.codegen.layout import build_layouts
from schemashift.codegen.migration import migration_plan
from schemashift.codegen.runtime_migrate import migrate
from schemashift.codegen.values import materialize
from schemashift.evolve import evolve, render_script
from schemashift.pipeline import CompileOptions, CompileResult
from tests.integration.harness import setup_and_migrate

pytestmark = pytest.mark.integration

SCHEMA = """
CREATE TABLE users (
  id INT PRIMARY KEY, fullname TEXT NOT NULL, email TEXT NOT NULL UNIQUE, age INT, bio TEXT);
CREATE TABLE orders (
  id INT PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  total NUMERIC(10,2) NOT NULL, note TEXT);
CREATE TABLE tags (id INT PRIMARY KEY, label TEXT NOT NULL);
"""
DATA = """
INSERT INTO users VALUES (1, 'Ann Lee', 'ann@x.io', 31, 'hello'), (2, 'Bob Roy', 'bob@x.io', NULL, NULL),
  (3, 'Cy Dunn', 'cy@x.io', 55, 'bio3');
INSERT INTO orders VALUES (10, 1, 12.50, 'first'), (11, 1, 7.25, NULL), (12, 3, 99.99, 'big');
INSERT INTO tags VALUES (1, 'sale'), (2, 'new');
"""
ALTER = """
ALTER TABLE users ADD COLUMN nick VARCHAR(20) NOT NULL DEFAULT 'anon';
ALTER TABLE users RENAME COLUMN fullname TO name;
ALTER TABLE users DROP COLUMN bio;
ALTER TABLE users ALTER COLUMN age TYPE BIGINT;
ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'new';
ALTER TABLE orders RENAME COLUMN note TO memo;
ALTER TABLE tags ADD COLUMN weight INT DEFAULT 5;
ALTER TABLE orders ADD CONSTRAINT chk_total CHECK (total >= 0);
CREATE INDEX ix_users_age ON users (age);
CREATE TABLE brands (id INT PRIMARY KEY, title TEXT NOT NULL);
DROP INDEX IF EXISTS no_such_index;
"""


def _canon(value: Any) -> Any:
    if isinstance(value, Decimal128):
        return ("dec", str(value.to_decimal()))
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def snapshot(db: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    info = {c["name"]: c for c in db.list_collections()}
    for name in sorted(info):
        if name.startswith(("_evolve", "_archive", "system.")):
            continue
        docs = sorted((_canon(d) for d in db[name].find()), key=lambda d: repr(d.get("_id")))
        indexes = sorted(
            (ix["name"], tuple(ix["key"].items()), bool(ix.get("unique")))
            for ix in db[name].list_indexes()
        )
        options = info[name].get("options", {})
        out[name] = {
            "docs": docs,
            "indexes": indexes,
            "validator": _canon(options.get("validator")),
            "level": options.get("validationLevel"),
        }
    return out


def fresh_migrate(
    result: CompileResult, options: CompileOptions, conn: Any, schema: str, db: Any
) -> None:
    layouts = build_layouts(result.graph, result.plan)
    for spec in build_collections(result.schema_, layouts, options.uuid_as).collections:
        db.create_collection(
            spec.name,
            validator=materialize(spec.validator),
            validationLevel=spec.validation_level,
            validationAction=spec.validation_action,
        )
    for ix in build_indexes(result.schema_, result.graph, layouts):
        kwargs: dict[str, Any] = {"name": ix.name}
        if ix.unique:
            kwargs["unique"] = True
        if ix.partial_filter:
            kwargs["partialFilterExpression"] = materialize(ix.partial_filter)
        db[ix.collection].create_index(list(ix.keys), **kwargs)
    migrate(
        conn,
        db,
        migration_plan(result.schema_, layouts, options),
        schema=schema,
        log=lambda *_: None,
    )


def load_script(text: str) -> dict[str, Any]:
    ns: dict[str, Any] = {"__name__": "evolve_generated"}
    import sys
    import types

    mod = types.ModuleType("evolve_generated")
    sys.modules["evolve_generated"] = mod
    exec(compile(text, "evolve_generated.py", "exec"), mod.__dict__)  # noqa: S102 - our own output
    ns.update(mod.__dict__)
    return ns


def run_case(
    pg_schema: Any,
    mongo_client: Any,
    schema_sql: str,
    data_sql: str,
    alter_sql: str,
    *,
    options: CompileOptions | None = None,
    new_options: CompileOptions | None = None,
    new_pg_sql: str | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], Any]:
    conn, schema = pg_schema
    options = options or CompileOptions()
    a_name, b_name = f"t_{uuid.uuid4().hex[:12]}", f"t_{uuid.uuid4().hex[:12]}"
    db_a, db_b = mongo_client[a_name], mongo_client[b_name]
    try:
        setup_and_migrate(schema_sql, data_sql, "", None, conn, schema, db_a, options)
        plan, old, new = evolve(
            schema_sql, alter_sql=alter_sql, options=options, new_options=new_options
        )
        before = snapshot(db_a)
        ns = load_script(render_script(plan))
        ns["apply"](db_a, log=lambda *_: None)
        conn.execute(
            new_pg_sql if new_pg_sql is not None else alter_sql
        )  # PostgreSQL does it for real
        fresh_migrate(new, new_options or options, conn, schema, db_b)
        after_incremental, fresh = snapshot(db_a), snapshot(db_b)
        rolled = None
        if not any(s.manual for s in plan.steps):
            ns["rollback"](db_a, log=lambda *_: None)
            rolled = snapshot(db_a)
        return plan, before, after_incremental, fresh, rolled
    finally:
        mongo_client.drop_database(a_name)
        mongo_client.drop_database(b_name)


def test_incremental_migration_equals_a_fresh_migration(pg_schema: Any, mongo_client: Any) -> None:
    plan, before, after, fresh, rolled = run_case(pg_schema, mongo_client, SCHEMA, DATA, ALTER)
    assert not any(s.manual for s in plan.steps), [s.description for s in plan.steps if s.manual]
    kinds = {s.kind for s in plan.steps}
    assert {
        "ADD_FIELD",
        "UNSET_FIELD",
        "RENAME_FIELD",
        "RETYPE_FIELD",
        "CREATE_COLLECTION",
    } <= kinds
    assert after == fresh
    # the embedded orders were rewritten too, not only the users documents
    users = {d["_id"]: d for d in after["users"]["docs"]}
    assert users[1]["name"] == "Ann Lee" and users[1]["nick"] == "anon" and "bio" not in users[1]
    assert rolled == before  # rolling the script back restores the v1 state


def test_every_step_has_the_reversibility_it_claims(pg_schema: Any, mongo_client: Any) -> None:
    plan, *_ = run_case(pg_schema, mongo_client, SCHEMA, DATA, ALTER)
    by_kind = {s.kind: s for s in plan.steps}
    assert by_kind["UNSET_FIELD"].reversibility == "REVERSIBLE_WITH_SAVED_DATA"
    assert by_kind["UNSET_FIELD"].saved_data == "_evolve_backup"
    assert by_kind["RENAME_FIELD"].reversibility == "REVERSIBLE"
    assert all(s.rollback for s in plan.steps if s.reversibility != "IRREVERSIBLE")
    assert all(
        s.rollback is None and s.recovery for s in plan.steps if s.reversibility == "IRREVERSIBLE"
    )


RESTRUCTURE_SCHEMA = """
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id), total INT);
"""
RESTRUCTURE_DATA = """
INSERT INTO customers VALUES (1, 'a'), (2, 'b'), (3, 'c');
INSERT INTO orders VALUES (10, 1, 5), (11, 1, 7), (12, 2, 9);
"""


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [("EMBED", "REFERENCE", "EXTRACT_EMBEDDED"), ("REFERENCE", "EMBED", "EMBED_COLLECTION")],
)
def test_embed_reference_restructuring_matches_a_fresh_migration(
    before: str, after: str, kind: str, pg_schema: Any, mongo_client: Any
) -> None:
    rel = "fk:orders.customer_id->customers.id"
    o1 = CompileOptions(relationship_overrides={rel: before})  # type: ignore[dict-item]
    o2 = CompileOptions(relationship_overrides={rel: after})  # type: ignore[dict-item]
    plan, v1, incremental, fresh, rolled = run_case(
        pg_schema,
        mongo_client,
        RESTRUCTURE_SCHEMA,
        RESTRUCTURE_DATA,
        "ALTER TABLE orders ADD COLUMN note TEXT;",
        options=o1,
        new_options=o2,
        new_pg_sql="ALTER TABLE orders ADD COLUMN note TEXT;",
    )
    assert [c.restructures for c in plan.plan_changes] == [True]
    assert any(s.kind == kind for s in plan.steps), [s.kind for s in plan.steps]
    assert incremental == fresh
    assert rolled == v1  # and the rollback restores the original layout
