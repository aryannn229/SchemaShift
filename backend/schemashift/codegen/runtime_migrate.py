from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from bson import Binary, Decimal128, ObjectId
from psycopg import sql
from psycopg.rows import dict_row
from pymongo import ReplaceOne

# --- Runtime of the generated Postgres -> MongoDB migration. ---------------------------
# The same code is imported by SchemaShift's verification harness and embedded verbatim in
# the generated migrate.py. PLAN (table layouts and column types) is generated per schema.


def convert_scalar(value: Any, base: str, uuid_as: str = "binary") -> Any:
    if value is None:
        return None
    if base == "DECIMAL":
        return Decimal128(str(value))
    if base in ("FLOAT", "DOUBLE"):
        return float(value)
    if base in ("INTEGER", "SMALLINT", "BIGINT"):
        return int(value)
    if base == "BOOLEAN":
        return bool(value)
    if base in ("TIMESTAMP", "TIMESTAMPTZ"):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value
    if base == "DATE":
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=UTC)
        return value
    if base == "TIME":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if base == "INTERVAL":
        if isinstance(value, timedelta):
            return f"PT{value.total_seconds():g}S"
        return str(value)
    if base == "UUID":
        value = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if uuid_as == "string" else Binary.from_uuid(value)
    if base == "BYTEA":
        return bytes(value)
    if base in ("JSON", "JSONB"):
        return value
    return str(value) if not isinstance(value, str) else value


def convert_value(value: Any, ctype: dict[str, Any], uuid_as: str = "binary") -> Any:
    if value is None:
        return None
    if ctype.get("is_array") and isinstance(value, (list, tuple)):
        return [
            convert_value(v, {**ctype, "is_array": isinstance(v, (list, tuple))}, uuid_as)
            for v in value
            if v is not None
        ]
    return convert_scalar(value, ctype["base"], uuid_as)


def synthetic_id(table: str, values: Any) -> ObjectId:
    """Deterministic ObjectId so that re-running the migration is idempotent."""
    payload = json.dumps([table, values], default=str, sort_keys=True).encode()
    return ObjectId(hashlib.sha1(payload).digest()[:12])


def row_to_doc(plan: dict[str, Any], table: str, row: dict[str, Any]) -> dict[str, Any]:
    """One SQL row -> document (without nested children). NULLs are omitted (absent field)."""
    t = plan["tables"][table]
    uuid_as = plan["options"]["uuid_as"]
    doc: dict[str, Any] = {}
    if t["kind"] == "root" and t["id_column"] is None:
        key_cols = t["pk"] or list(t["columns"])
        doc["_id"] = synthetic_id(table, [str(row.get(c)) for c in key_cols])
    for col, ctype in t["columns"].items():
        if col in t["dropped"] or col == t.get("scalar_column"):
            continue
        value = convert_value(row.get(col), ctype, uuid_as)
        if value is None:
            continue
        doc[t["fields"][col]] = value
    return doc


def children_of(plan: dict[str, Any], table: str) -> list[str]:
    return sorted(n for n, t in plan["tables"].items() if t["host"] == table)


def _fetch_children(
    conn: Any, plan: dict[str, Any], child: str, keys: list[tuple[Any, ...]], schema: str
) -> list[dict[str, Any]]:
    t = plan["tables"][child]
    fk = t["dropped"]
    if not keys:
        return []
    order = sql.SQL(", ").join(sql.Identifier(c) for c in (t["pk"] or list(t["columns"])))
    ident = sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(child)])
    with conn.cursor(row_factory=dict_row) as cur:
        if len(fk) == 1:
            query = sql.SQL("SELECT * FROM {} WHERE {} = ANY(%s) ORDER BY {}").format(
                ident, sql.Identifier(fk[0]), order
            )
            cur.execute(query, ([k[0] for k in keys],))
        else:
            cond = sql.SQL(" OR ").join(
                sql.SQL("({})").format(
                    sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in fk)
                )
                for _ in keys
            )
            query = sql.SQL("SELECT * FROM {} WHERE {} ORDER BY {}").format(ident, cond, order)
            cur.execute(query, [v for k in keys for v in k])
        return list(cur.fetchall())


def build_documents(
    conn: Any, plan: dict[str, Any], table: str, rows: list[dict[str, Any]], schema: str
) -> list[dict[str, Any]]:
    """Documents for ``rows`` including all embedded children (recursively)."""
    docs = [row_to_doc(plan, table, r) for r in rows]
    for child in children_of(plan, table):
        ct = plan["tables"][child]
        key_cols, fk_cols = ct["host_columns"], ct["dropped"]
        keys = sorted(
            {
                tuple(r[c] for c in key_cols)
                for r in rows
                if all(r.get(c) is not None for c in key_cols)
            },
            key=repr,
        )
        child_rows = _fetch_children(conn, plan, child, keys, schema)
        child_docs = build_documents(conn, plan, child, child_rows, schema)
        grouped: dict[tuple[Any, ...], list[Any]] = {}
        uuid_as = plan["options"]["uuid_as"]
        for crow, cdoc in zip(child_rows, child_docs, strict=True):
            element: Any = cdoc
            if ct["kind"] == "ref_scalars":
                element = convert_value(
                    crow[ct["scalar_column"]], ct["columns"][ct["scalar_column"]], uuid_as
                )
            grouped.setdefault(tuple(crow[c] for c in fk_cols), []).append(element)
        name = ct["path"][-1]
        for row, doc in zip(rows, docs, strict=True):
            found = grouped.get(tuple(row.get(c) for c in key_cols))
            if not found:
                continue
            doc[name] = found[0] if ct["kind"] == "object" else found
    return docs


def write_batch(db: Any, collection: str, docs: list[dict[str, Any]]) -> None:
    if docs:
        db[collection].bulk_write(
            [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs], ordered=False
        )


def migrate(
    conn: Any,
    db: Any,
    plan: dict[str, Any],
    schema: str = "public",
    batch_size: int = 1000,
    log: Any = print,
) -> dict[str, int]:
    """Copy every root table (with embedded children) into MongoDB. Idempotent (upserts by _id)."""
    counts: dict[str, int] = {}
    for table in plan["roots"]:
        t = plan["tables"][table]
        total = 0
        ident = sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(table)])
        order = sql.SQL(", ").join(sql.Identifier(c) for c in (t["pk"] or list(t["columns"])))
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL("SELECT * FROM {} ORDER BY {}").format(ident, order))
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                write_batch(db, t["collection"], build_documents(conn, plan, table, rows, schema))
                total += len(rows)
        counts[t["collection"]] = total
        log(f"{t['collection']}: {total} document(s)")
    if plan["options"]["preserve_integer_ids"]:
        _seed_counters(conn, db, plan, schema)
    return counts


def _seed_counters(conn: Any, db: Any, plan: dict[str, Any], schema: str) -> None:
    for table in plan["roots"]:
        t = plan["tables"][table]
        for col in t["identity"]:
            ident = sql.SQL(".").join([sql.Identifier(schema), sql.Identifier(table)])
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(col), ident))
                top = cur.fetchone()[0] or 0
            db["_counters"].replace_one(
                {"_id": f"{table}.{col}"}, {"_id": f"{table}.{col}", "seq": int(top)}, upsert=True
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate PostgreSQL data into MongoDB")
    parser.add_argument(
        "--source", default=os.environ.get("SOURCE_DATABASE_URL"), help="PostgreSQL DSN"
    )
    parser.add_argument("--target", default=os.environ.get("MONGO_URL"), help="MongoDB URI")
    parser.add_argument("--database", default=os.environ.get("MONGO_DATABASE", "migrated"))
    parser.add_argument("--schema", default="public", help="PostgreSQL schema")
    args = parser.parse_args(argv)
    if not args.source or not args.target:
        parser.error("--source and --target (or SOURCE_DATABASE_URL / MONGO_URL) are required")
    from pymongo import MongoClient

    client: MongoClient[Any] = MongoClient(args.target)
    with psycopg.connect(args.source) as conn, client:
        counts = migrate(conn, client[args.database], globals()["PLAN"], args.schema)
    print("done:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
