"""Phase 22 (pure parts): ALTER subset, differ, rename candidates, plan classification, script rendering."""

from __future__ import annotations

import ast

import pytest

from schemashift.evolve import apply_alters, diff_schemas, evolve, plan_evolution, render_script
from schemashift.evolve.alter import split_statements
from schemashift.parser import parse
from schemashift.pipeline import CompileOptions

OLD = """
CREATE TABLE users (id SERIAL PRIMARY KEY, fullname TEXT NOT NULL, email TEXT UNIQUE, age INT);
CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  total NUMERIC(10,2));
"""


def schema(sql: str):  # type: ignore[no-untyped-def]
    return parse(sql).schema_


def kinds(diff) -> list[str]:  # type: ignore[no-untyped-def]
    return [o.kind for o in diff.ops]


# ------------------------------------------------------------------------------ ALTER subset
def test_split_statements_respects_quotes_comments_and_parens() -> None:
    script = "ALTER TABLE a ADD COLUMN x TEXT DEFAULT 'a;b'; -- c;\n/* d; */ DROP TABLE b;"
    assert split_statements(script) == [
        "ALTER TABLE a ADD COLUMN x TEXT DEFAULT 'a;b'",
        "DROP TABLE b",
    ]


def test_alter_subset_produces_the_expected_schema() -> None:
    new, warnings, renames = apply_alters(
        schema(OLD),
        """
        ALTER TABLE users ADD COLUMN nick VARCHAR(20) NOT NULL DEFAULT 'anon';
        ALTER TABLE users RENAME COLUMN fullname TO name;
        ALTER TABLE users ALTER COLUMN age TYPE BIGINT;
        ALTER TABLE users ALTER COLUMN age SET NOT NULL;
        ALTER TABLE users DROP CONSTRAINT users_email_key;
        ALTER TABLE orders ADD CONSTRAINT chk CHECK (total >= 0);
        ALTER TABLE orders ALTER COLUMN total SET DEFAULT 0, ADD COLUMN note TEXT;
        CREATE INDEX ix_total ON orders (total);
        ALTER TABLE users CLUSTER ON something;
        """,
    )
    users, orders = new.tables["users"], new.tables["orders"]
    assert [c.name for c in users.columns] == ["id", "name", "email", "age", "nick"]
    assert users.column("nick").default.sql == "'anon'"  # type: ignore[union-attr]
    assert users.column("age").sql_type.base == "BIGINT" and not users.column("age").nullable  # type: ignore[union-attr]
    assert users.uniques == ()
    assert orders.checks[0].name == "chk" and orders.indexes[0].columns == ("total",)
    assert orders.column("total").default.sql == "0"  # type: ignore[union-attr]
    assert renames == {"users.fullname": "name"}
    assert len(warnings) == 1 and "CLUSTER" in warnings[0]


def test_dropping_a_column_removes_what_depends_on_it() -> None:
    new, _, _ = apply_alters(schema(OLD), "ALTER TABLE orders DROP COLUMN user_id;")
    assert new.tables["orders"].foreign_keys == ()
    new, w, _ = apply_alters(schema(OLD), "DROP TABLE users;")
    assert "users" not in new.tables and new.tables["orders"].foreign_keys == ()
    assert any("foreign keys" in x for x in w)


def test_rename_table_updates_references() -> None:
    new, _, renames = apply_alters(schema(OLD), "ALTER TABLE users RENAME TO members;")
    assert new.tables["orders"].foreign_keys[0].ref_table == "members" and renames == {
        "users": "members"
    }


def test_unknown_table_is_reported_not_guessed() -> None:
    _, warnings, _ = apply_alters(schema(OLD), "ALTER TABLE ghosts ADD COLUMN x INT;")
    assert warnings and "ghosts" in warnings[0]


# ------------------------------------------------------------------------------ differ
def test_diff_of_identical_schemas_is_empty() -> None:
    assert diff_schemas(schema(OLD), schema(OLD)).ops == ()


def test_every_change_kind_is_detected() -> None:
    new = schema(
        """
        CREATE TABLE users (id SERIAL, nick TEXT NOT NULL DEFAULT 'x', email TEXT, age BIGINT,
          PRIMARY KEY (id, email), CHECK (age > 0));
        CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL, total NUMERIC(10,2) NOT NULL);
        CREATE UNIQUE INDEX ix ON orders (total);
        CREATE TABLE extra (id INT PRIMARY KEY);
        """
    )
    got = set(kinds(diff_schemas(schema(OLD), new)))
    assert {
        "ADD_COLUMN",
        "DROP_COLUMN",
        "RETYPE_COLUMN",
        "CHANGE_PRIMARY_KEY",
        "DROP_UNIQUE",
        "ADD_CHECK",
        "DROP_FOREIGN_KEY",
        "SET_NOT_NULL",
        "ADD_INDEX",
        "ADD_TABLE",
    } <= got


def test_renames_are_candidates_until_confirmed() -> None:
    new = schema(OLD.replace("fullname", "name"))
    d = diff_schemas(schema(OLD), new)
    assert "RENAME_COLUMN" not in kinds(d)  # drop + add: data would be lost
    assert {"DROP_COLUMN", "ADD_COLUMN"} <= set(kinds(d))
    (cand,) = [c for c in d.rename_candidates if c.kind == "column"]
    assert (cand.old, cand.new, cand.confirmed) == ("fullname", "name", False)
    d2 = diff_schemas(schema(OLD), new, {"users.fullname": "name"})
    assert "RENAME_COLUMN" in kinds(d2) and "DROP_COLUMN" not in kinds(d2)
    assert [c.confirmed for c in d2.rename_candidates if c.kind == "column"] == [True]


def test_table_rename_candidate_and_confirmation() -> None:
    new = schema(OLD.replace("users", "members"))
    d = diff_schemas(schema(OLD), new)
    assert any(
        c.kind == "table" and c.old == "users" and c.new == "members" for c in d.rename_candidates
    )
    assert "RENAME_TABLE" not in kinds(d)
    assert "RENAME_TABLE" in kinds(diff_schemas(schema(OLD), new, {"users": "members"}))


# ------------------------------------------------------------------------------ plan
def test_plan_classifies_steps_and_gives_rollback_only_where_reversible() -> None:
    plan = plan_evolution(
        OLD,
        alter_sql="""
        ALTER TABLE users ADD COLUMN nick TEXT DEFAULT 'x';
        ALTER TABLE users RENAME COLUMN fullname TO name;
        ALTER TABLE users DROP COLUMN age;
        ALTER TABLE users ALTER COLUMN email TYPE INT;
        ALTER TABLE orders DROP CONSTRAINT orders_pkey;
        ALTER TABLE orders ADD PRIMARY KEY (user_id);
        """,
    )
    by = {s.kind: s for s in plan.steps if not s.manual}
    assert by["ADD_FIELD"].reversibility == "REVERSIBLE"
    assert by["RENAME_FIELD"].reversibility == "REVERSIBLE"
    assert by["UNSET_FIELD"].reversibility == "REVERSIBLE_WITH_SAVED_DATA"
    assert (
        by["RETYPE_FIELD"].reversibility == "REVERSIBLE_WITH_SAVED_DATA"
    )  # text -> int may lose data
    manual = [s for s in plan.steps if s.manual]
    assert manual and all(
        s.reversibility == "IRREVERSIBLE" and s.recovery and s.rollback is None for s in manual
    )
    assert all(s.rollback for s in plan.steps if s.reversibility != "IRREVERSIBLE")
    ids = [s.id for s in plan.steps]
    assert all(
        d in ids and ids.index(d) < ids.index(s.id) for s in plan.steps for d in s.depends_on
    )


def test_widening_conversion_is_reversible_without_a_backup() -> None:
    plan = plan_evolution(OLD, alter_sql="ALTER TABLE users ALTER COLUMN age TYPE BIGINT;")
    (step,) = [s for s in plan.steps if s.kind == "RETYPE_FIELD"]
    assert step.reversibility == "REVERSIBLE" and "backup" not in step.code


def test_dropped_table_is_archived_not_deleted() -> None:
    plan = plan_evolution(OLD, alter_sql="DROP TABLE orders;")
    steps = [s for s in plan.steps if s.kind in ("ARCHIVE_COLLECTION", "UNSET_FIELD")]
    assert steps and all(s.reversibility == "REVERSIBLE_WITH_SAVED_DATA" for s in steps)
    assert not any("drop_collection" in s.code or ".drop()" in s.code for s in plan.steps)


def test_guarantee_diff_names_the_rule_and_the_change() -> None:
    plan = plan_evolution(OLD, alter_sql="ALTER TABLE users DROP CONSTRAINT users_email_key;")
    removed = [c for c in plan.guarantees.changes if c.direction == "removed"]
    assert any(c.node_id == "unique:users.email" and c.rule_id == "EQ-VALUE-UNIQ" for c in removed)
    hit = next(c for c in removed if c.node_id == "unique:users.email")
    assert any("UNIQUE (email)" in b for b in hit.because)


def test_guarantee_diff_reports_added_guarantees_with_their_status() -> None:
    plan = plan_evolution(OLD, alter_sql="ALTER TABLE users ADD CONSTRAINT c CHECK (age > 0);")
    added = [c for c in plan.guarantees.changes if c.direction == "added"]
    assert any(c.rule_id == "EQ-CHECK" and c.new in ("SAFE", "CHANGED", "BROKEN") for c in added)
    assert plan.guarantees.regressions == 0


def test_plan_changes_report_restructuring() -> None:
    rel = "fk:orders.user_id->users.id"
    plan, old, new = evolve(
        OLD,
        alter_sql="ALTER TABLE orders ADD COLUMN note TEXT;",
        options=CompileOptions(relationship_overrides={rel: "EMBED"}),  # type: ignore[dict-item]
        new_options=CompileOptions(relationship_overrides={rel: "REFERENCE"}),  # type: ignore[dict-item]
    )
    (change,) = plan.plan_changes
    assert (change.old, change.new, change.restructures) == ("EMBED", "REFERENCE", True)
    assert any(s.kind == "EXTRACT_EMBEDDED" for s in plan.steps)
    assert old.plan.decision(rel) == "EMBED" and new.plan.decision(rel) == "REFERENCE"


def test_rendered_script_is_valid_python_with_apply_and_rollback() -> None:
    plan = plan_evolution(
        OLD,
        alter_sql="ALTER TABLE users ADD COLUMN nick TEXT DEFAULT 'x'; ALTER TABLE users DROP COLUMN age;",
    )
    text = render_script(plan)
    tree = ast.parse(text)
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"apply", "rollback"} <= names
    assert all(f"step_{s.id}" in names for s in plan.steps if not s.manual)
    assert "def rollback_" in text and "STEPS = " in text


@pytest.mark.parametrize("stmt", ["", "   ;  ;", "-- nothing"])
def test_empty_alter_scripts_change_nothing(stmt: str) -> None:
    new, warnings, renames = apply_alters(schema(OLD), stmt)
    assert new == schema(OLD) and warnings == [] and renames == {}
