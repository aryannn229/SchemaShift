"""Guarantee probes: concrete demonstrations of CHANGED / BROKEN verdicts on the sandbox data.

Each probe performs the same logical action on PostgreSQL (inside a transaction that is rolled
back) and on MongoDB (with a snapshot that is restored), then compares the resulting tables.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from pymongo.errors import PyMongoError

from schemashift.codegen.runtime_migrate import convert_value, row_to_doc
from schemashift.ir.nodes import (
    CascadingDelete,
    CascadingUpdate,
    IRNode,
    ReferentialIntegrity,
    RestrictDelete,
    SetDefaultOnDelete,
    SetNullOnDelete,
)
from schemashift.models.verdict import Verdict
from schemashift.semantic.graph import Relationship
from schemashift.verification.ddl import q
from schemashift.verification.runner import Env, ProbeOutcome, restore_mongo, snapshot_mongo

MAX_PROBES = 20
CANDIDATES = 25
INT_TYPES = ("INTEGER", "BIGINT", "SMALLINT")
Probe = Callable[[Env, Verdict, Any], ProbeOutcome | None]


def _pg_attempt(env: Env, sql: str, params: tuple[Any, ...]) -> str | None:
    """Run a statement in a savepoint and roll it back; the error text, or None on success."""
    pg = env.sb.pg
    pg.execute("BEGIN")
    try:
        pg.execute("SAVEPOINT attempt")
        try:
            pg.execute(sql, params)
            return None
        except psycopg.Error as exc:
            return str(exc).splitlines()[0]
    finally:
        pg.execute("ROLLBACK")


def _demonstrate(
    env: Env,
    verdict: Verdict,
    description: str,
    pg_sql: str,
    pg_params: tuple[Any, ...],
    mongo_action: Callable[[Any], str],
) -> ProbeOutcome:
    pg, db = env.sb.pg, env.sb.mongo_db
    snap = snapshot_mongo(db)
    pg.execute("BEGIN")
    try:
        pg.execute("SAVEPOINT probe")
        try:
            cur = pg.execute(pg_sql, pg_params)
            pg_text = f"statement succeeded ({cur.rowcount} row(s) affected)"
        except psycopg.Error as exc:
            pg.execute("ROLLBACK TO SAVEPOINT probe")
            pg_text = "statement REJECTED: " + str(exc).splitlines()[0]
        try:
            mongo_text = mongo_action(db)
        except PyMongoError as exc:
            mongo_text = "operation REJECTED: " + str(exc).splitlines()[0]
        diffs = env.snapshot_tables()
    finally:
        pg.execute("ROLLBACK")
        restore_mongo(db, snap)
    differing = [t for t, d in diffs.items() if d.status == "MISMATCH"]
    examples = diffs[differing[0]].differing[:3] if differing else []
    detail = ", ".join(
        f"{t}: PostgreSQL {diffs[t].pg_rows} row(s) vs MongoDB {diffs[t].mongo_rows}"
        for t in differing
    )
    return ProbeOutcome(
        verdict_id=verdict.node_id,
        rule_id=verdict.rule_id,
        status=verdict.status,
        description=description,
        postgres=pg_text,
        mongo=mongo_text
        + (
            f"; resulting difference: {detail}"
            if detail
            else "; the resulting tables are identical"
        ),
        demonstrates=bool(differing),
        tables_differing=differing,
        examples=examples,
    )


def _ctype(env: Env, table: str, column: str) -> dict[str, Any]:
    plan = env.migrate_ns["PLAN"] if env.migrate_ns else {}
    return dict(plan["tables"][table]["columns"][column])


# ----------------------------------------------------------------- parent delete
def _candidate_parents(env: Env, rel: Relationship) -> list[tuple[Any, ...]]:
    """Parent keys that have at least one child through ``rel`` (a few candidates)."""
    cond = " AND ".join(
        f"c.{q(cc)} = p.{q(pc)}" for cc, pc in zip(rel.fk.columns, rel.fk.ref_columns, strict=True)
    )
    cols = ", ".join(f"p.{q(pc)}" for pc in rel.fk.ref_columns)
    rows = env.sb.pg.execute(
        f"SELECT {cols} FROM {q(rel.parent)} p WHERE EXISTS "
        f"(SELECT 1 FROM {q(rel.child)} c WHERE {cond}) ORDER BY {cols} LIMIT {CANDIDATES}"
    ).fetchall()
    return [tuple(r) for r in rows]


def _delete_probe(kind: str, expect_rejection: bool) -> Probe:
    """Pick a parent that actually exercises *this* guarantee: for RESTRICT one that PostgreSQL
    refuses to delete, for the other actions one it deletes (other foreign keys may block)."""

    def probe(env: Env, verdict: Verdict, node: Any) -> ProbeOutcome | None:
        rel = env.result.graph.relationships.get(node.fk_ref)
        if rel is None or not env.layouts[rel.parent].is_root:
            return None
        where = " AND ".join(f"{q(c)} = %s" for c in rel.fk.ref_columns)
        delete = f"DELETE FROM {q(rel.parent)} WHERE {where}"
        chosen: tuple[Any, ...] | None = None
        for key in _candidate_parents(env, rel):
            rejected = _pg_attempt(env, delete, key) is not None
            if rejected == expect_rejection:
                chosen = key
                break
        if chosen is None:
            return None
        layout = env.layouts[rel.parent]
        filt = {
            layout.fields[c]: convert_value(v, _ctype(env, rel.parent, c), env.options.uuid_as)
            for c, v in zip(rel.fk.ref_columns, chosen, strict=True)
        }

        def on_mongo(db: Any) -> str:
            res = db[layout.collection].delete_one(filt)
            return f"deleted {res.deleted_count} parent document (no cascade, no checks)"

        label = ", ".join(f"{c}={v!r}" for c, v in zip(rel.fk.ref_columns, chosen, strict=True))
        return _demonstrate(
            env,
            verdict,
            f"{kind}: delete {rel.parent}({label}), which still has {rel.child} rows",
            delete,
            chosen,
            on_mongo,
        )

    return probe


# ------------------------------------------------------------ referential integrity
def _fresh_value(env: Env, table: str, column: str, value: Any) -> Any | None:
    """A value that does not collide with existing rows, or None when we cannot make one."""
    col = env.schema.tables[table].column(column)
    if col is None or value is None:
        return value
    base = col.sql_type.base
    if base in INT_TYPES:
        top = env.sb.pg.execute(f"SELECT max({q(column)}) FROM {q(table)}").fetchone()
        return int(top[0] or 0) + 1000
    if base in ("TEXT", "VARCHAR", "CHAR") and isinstance(value, str):
        suffix = "~probe"
        limit = col.sql_type.length
        fresh = value.rstrip() + suffix
        if limit is not None:
            fresh = value.rstrip()[: max(0, limit - len(suffix))] + suffix
        return fresh if (limit is None or len(fresh) <= limit) else None
    return None


def _orphan_probe(env: Env, verdict: Verdict, node: ReferentialIntegrity) -> ProbeOutcome | None:
    layout = env.layouts[node.child]
    if not layout.is_root:
        return None
    table = env.schema.tables[node.child]
    parent_table = env.schema.tables[node.parent]
    row = env.sb.pg.execute(f"SELECT * FROM {q(node.child)} LIMIT 1").fetchone()
    if row is None:
        return None
    cols = [c.name for c in table.columns]
    values = dict(zip(cols, row, strict=True))
    unique_cols: set[str] = set(table.primary_key.columns if table.primary_key else ())
    for u in table.uniques:
        unique_cols.update(u.columns)
    for ix in table.indexes:
        if ix.unique:
            unique_cols.update(ix.columns)
    for col in sorted(unique_cols - set(node.columns)):
        fresh = _fresh_value(env, node.child, col, values[col])
        if fresh is None and values[col] is not None:
            return None
        values[col] = fresh
    for fk_col, ref_col in zip(node.columns, node.ref_columns, strict=True):
        ref = parent_table.column(ref_col)
        if ref is None or ref.sql_type.base not in INT_TYPES:
            return None
        top = env.sb.pg.execute(f"SELECT max({q(ref_col)}) FROM {q(node.parent)}").fetchone()
        values[fk_col] = int(top[0] or 0) + 1000
    insert = (
        f"INSERT INTO {q(node.child)} ({', '.join(q(c) for c in cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))})"
    )
    plan = env.migrate_ns["PLAN"] if env.migrate_ns else {}

    def on_mongo(db: Any) -> str:
        db[layout.collection].insert_one(row_to_doc(plan, node.child, values))
        return "inserted the orphan document (MongoDB has no foreign keys)"

    label = ", ".join(f"{c}={values[c]!r}" for c in node.columns)
    return _demonstrate(
        env,
        verdict,
        f"insert a {node.child} row whose {label} references a {node.parent} that does not exist",
        insert,
        tuple(values[c] for c in cols),
        on_mongo,
    )


# ------------------------------------------------------------------ key update
def _key_update_probe(env: Env, verdict: Verdict, node: CascadingUpdate) -> ProbeOutcome | None:
    rel = env.result.graph.relationships.get(node.fk_ref)
    if rel is None or len(rel.fk.ref_columns) != 1 or not env.layouts[rel.parent].is_root:
        return None
    pcol = rel.fk.ref_columns[0]
    column = env.schema.tables[rel.parent].column(pcol)
    if column is None or column.sql_type.base not in INT_TYPES:
        return None
    candidates = _candidate_parents(env, rel)
    if not candidates:
        return None
    key = candidates[0]
    top = env.sb.pg.execute(f"SELECT max({q(pcol)}) FROM {q(rel.parent)}").fetchone()
    new = int(top[0]) + 1000
    layout = env.layouts[rel.parent]
    field = layout.fields[pcol]

    def on_mongo(db: Any) -> str:
        res = db[layout.collection].update_one({field: key[0]}, {"$set": {field: new}})
        return f"updated {res.modified_count} parent document (children keep the old key)"

    return _demonstrate(
        env,
        verdict,
        f"change {rel.parent}.{pcol} from {key[0]!r} to {new!r} "
        f"while {rel.child} rows reference it",
        f"UPDATE {q(rel.parent)} SET {q(pcol)} = %s WHERE {q(pcol)} = %s",
        (new, key[0]),
        on_mongo,
    )


PROBES: dict[type[IRNode], Probe] = {
    CascadingDelete: _delete_probe("ON DELETE CASCADE", expect_rejection=False),
    SetNullOnDelete: _delete_probe("ON DELETE SET NULL", expect_rejection=False),
    SetDefaultOnDelete: _delete_probe("ON DELETE SET DEFAULT", expect_rejection=False),
    RestrictDelete: _delete_probe("ON DELETE RESTRICT / NO ACTION", expect_rejection=True),
    ReferentialIntegrity: _orphan_probe,
    CascadingUpdate: _key_update_probe,
}


def run_probes(env: Env) -> list[ProbeOutcome]:
    """Probe every CHANGED/BROKEN verdict we know how to demonstrate (at most ``MAX_PROBES``)."""
    out: list[ProbeOutcome] = []
    for v in env.result.equivalence.final.verdicts:
        if v.status == "SAFE":
            continue
        node = env.result.ir.by_id(v.node_id)
        fn = PROBES.get(type(node))
        if fn is None:
            continue
        outcome = fn(env, v, node)
        if outcome is not None:
            out.append(outcome)
        if len(out) >= MAX_PROBES:
            break
    return out
