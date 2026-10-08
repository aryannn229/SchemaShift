from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId

try:
    from schemashift.codegen.runtime_migrate import convert_value, row_to_doc
except ImportError:  # generated deployment: migrate.py sits next to helpers.py
    from migrate import convert_value, row_to_doc  # type: ignore[import-not-found,no-redef]

# --- Runtime of the generated application-level enforcement helpers. -------------------
# MongoDB has no foreign keys, cascades, defaults or sequences. These helpers re-create the
# guarantees that SchemaShift's equivalence checker marked CHANGED or BROKEN. PLAN (layouts,
# relationships, defaults, unique keys) is generated per schema.


class IntegrityError(Exception):
    """A SQL guarantee (foreign key, restrict, uniqueness) would be violated."""


def run_in_transaction(client: Any, fn: Any) -> Any:
    """Run ``fn(session)`` atomically (needs a replica set or Atlas)."""
    with client.start_session() as session:
        return session.with_transaction(fn)


def next_id(db: Any, key: str, session: Any = None) -> int:
    """Counter-collection replacement for SERIAL/IDENTITY (preserve_integer_ids)."""
    doc = db["_counters"].find_one_and_update(
        {"_id": key}, {"$inc": {"seq": 1}}, upsert=True, return_document=True, session=session
    )
    return int(doc["seq"])


# ----------------------------------------------------------------------- navigation
def _bson(plan: dict[str, Any], table: str, col: str, value: Any) -> Any:
    return convert_value(value, plan["tables"][table]["columns"][col], plan["options"]["uuid_as"])


def _field(plan: dict[str, Any], table: str, col: str) -> str:
    t = plan["tables"][table]
    if col in t["dropped"]:
        host = plan["tables"][t["host"]]
        return str(host["fields"][t["host_columns"][t["dropped"].index(col)]])
    return str(t["fields"].get(col, col))


def _elements(node: Any, step: str) -> list[tuple[Any, Any]]:
    """(element, holder) pairs found under ``node[step]`` (arrays are expanded)."""
    value = node.get(step) if isinstance(node, dict) else None
    if value is None:
        return []
    if isinstance(value, list):
        return [(v, node) for v in value]
    return [(value, node)]


def _walk(doc: dict[str, Any], path: list[str]) -> list[tuple[Any, Any]]:
    """All (node, holder) at the end of ``path`` inside a root document."""
    current: list[tuple[Any, Any]] = [(doc, None)]
    for step in path:
        nxt: list[tuple[Any, Any]] = []
        for node, _ in current:
            nxt.extend(_elements(node, step))
        current = nxt
    return current


def _row_of(plan: dict[str, Any], table: str, node: Any, holder: Any) -> dict[str, Any]:
    """Flatten a document node back into a SQL-style row (keys are column names)."""
    t = plan["tables"][table]
    row: dict[str, Any] = {}
    if t["kind"] == "ref_scalars":
        row[t["scalar_column"]] = node
    else:
        for col, field in t["fields"].items():
            if field in node:
                row[col] = node[field]
    if t["host"] is not None and holder is not None:
        host = plan["tables"][t["host"]]
        for dropped, hcol in zip(t["dropped"], t["host_columns"], strict=True):
            hv = holder.get(host["fields"][hcol])
            if hv is not None:
                row[dropped] = hv
    return row


def locate(
    db: Any, plan: dict[str, Any], table: str, match: dict[str, Any], session: Any = None
) -> list[tuple[dict[str, Any], Any, dict[str, Any]]]:
    """Rows of ``table`` whose columns equal ``match``: (root document, node, row) triples."""
    t = plan["tables"][table]
    wanted = {c: _bson(plan, table, c, v) for c, v in match.items()}
    path = t["path"]
    query = {
        ".".join([*path, _field(plan, table, c)]) if path else _field(plan, table, c): v
        for c, v in wanted.items()
        if c not in t["dropped"] or t["host"] is None
    }
    if t["kind"] == "ref_scalars":
        query = {".".join(path): wanted[t["scalar_column"]]} if t["scalar_column"] in wanted else {}
    found: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
    for doc in db[t["collection"]].find(query, session=session):
        for node, holder in _walk(doc, path):
            row = _row_of(plan, table, node, holder if t["host"] else None)
            if all(row.get(c) == v for c, v in wanted.items()):
                found.append((doc, node, row))
    return found


def exists(
    db: Any, plan: dict[str, Any], table: str, match: dict[str, Any], session: Any = None
) -> bool:
    return bool(locate(db, plan, table, match, session))


def _identity_match(plan: dict[str, Any], table: str, row: dict[str, Any]) -> dict[str, Any]:
    t = plan["tables"][table]
    cols = t["pk"] or list(row)
    return {c: row[c] for c in cols if c in row}


def _positional(
    plan: dict[str, Any],
    table: str,
    last: str | None = "e",
    last_filter: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Update path to a table's nodes plus the array filters it needs.

    Outer arrays use ``$[oN]`` guarded by an ``$exists`` filter (a bare ``$[]`` fails when some
    element lacks the inner array); the last array uses ``$[last]`` with ``last_filter``.
    """
    chain: list[str] = []
    cur: str | None = table
    while cur is not None and plan["tables"][cur]["kind"] != "root":
        chain.append(cur)
        cur = plan["tables"][cur]["host"]
    chain.reverse()
    parts: list[str] = []
    filters: list[dict[str, Any]] = []
    for i, name in enumerate(chain):
        ct = plan["tables"][name]
        parts.append(ct["path"][-1])
        if ct["kind"] not in ("array", "ref_docs"):
            continue
        if i == len(chain) - 1:
            if last is not None:
                parts.append(f"$[{last}]")
                if last_filter:
                    filters.append(last_filter)
        else:
            parts.append(f"$[o{i}]")
            filters.append({f"o{i}.{plan['tables'][chain[i + 1]]['path'][-1]}": {"$exists": True}})
    return ".".join(parts), filters


# ----------------------------------------------------------------- update primitives
def _update_rows(
    db: Any,
    plan: dict[str, Any],
    table: str,
    match: dict[str, Any],
    set_: dict[str, Any] | None = None,
    unset: list[str] | None = None,
    session: Any = None,
) -> int:
    """Set or unset columns of every row of ``table`` matching ``match``."""
    t = plan["tables"][table]
    rows = locate(db, plan, table, match, session)
    if not rows:
        return 0
    if t["kind"] == "root":
        for doc, _, _ in rows:
            update: dict[str, Any] = {}
            if set_:
                update["$set"] = {t["fields"][c]: _bson(plan, table, c, v) for c, v in set_.items()}
            if unset:
                update["$unset"] = {t["fields"][c]: "" for c in unset}
            db[t["collection"]].update_one({"_id": doc["_id"]}, update, session=session)
        return len(rows)
    is_array = t["kind"] in ("array", "ref_docs")
    count = 0
    for doc, _, row in rows:
        ident_filter = None
        if is_array:
            ident = _identity_match(plan, table, row)
            ident_filter = {
                f"e.{t['fields'][c]}": _bson(plan, table, c, v) for c, v in ident.items()
            }
        prefix, filters = _positional(
            plan, table, last="e" if is_array else None, last_filter=ident_filter
        )
        update = {}
        if set_:
            update["$set"] = {
                f"{prefix}.{t['fields'][c]}": _bson(plan, table, c, v) for c, v in set_.items()
            }
        if unset:
            update["$unset"] = {f"{prefix}.{t['fields'][c]}": "" for c in unset}
        kwargs: dict[str, Any] = {"array_filters": filters} if filters else {}
        db[t["collection"]].update_one({"_id": doc["_id"]}, update, session=session, **kwargs)
        count += 1
    return count


def _remove_row(
    db: Any,
    plan: dict[str, Any],
    table: str,
    doc: dict[str, Any],
    row: dict[str, Any],
    session: Any,
) -> None:
    t = plan["tables"][table]
    coll = db[t["collection"]]
    if t["kind"] == "root":
        coll.delete_one({"_id": doc["_id"]}, session=session)
        return
    target, filters = _positional(plan, table, last=None)
    flt = {"_id": doc["_id"]}
    kwargs: dict[str, Any] = {"array_filters": filters} if filters else {}
    if t["kind"] == "ref_scalars":
        coll.update_one(
            flt, {"$pull": {target: row[t["scalar_column"]]}}, session=session, **kwargs
        )
    elif t["kind"] == "object":
        coll.update_one(flt, {"$unset": {target: ""}}, session=session, **kwargs)
    else:
        ident = {
            t["fields"][c]: _bson(plan, table, c, v)
            for c, v in _identity_match(plan, table, row).items()
        }
        coll.update_one(flt, {"$pull": {target: ident}}, session=session, **kwargs)


# ------------------------------------------------------------------------- delete
def _default_for(plan: dict[str, Any], table: str, col: str) -> Any:
    spec = plan["defaults"].get(table, {}).get(col)
    if spec is None or spec["kind"] != "literal":
        raise IntegrityError(f"{table}.{col} has no constant default for ON DELETE SET DEFAULT")
    return spec["value"]


def _apply_parent_rules(
    db: Any, plan: dict[str, Any], table: str, row: dict[str, Any], session: Any
) -> None:
    """Emulate ON DELETE actions for children of the row about to disappear."""
    for rel in plan["relationships"]:
        if rel["parent"] != table or rel["embedded"]:
            continue
        values = [row.get(c) for c in rel["ref_columns"]]
        if any(v is None for v in values):
            continue
        match = dict(zip(rel["columns"], values, strict=True))
        children = locate(db, plan, rel["child"], match, session)
        if not children:
            continue
        action = rel["on_delete"]
        if action in ("RESTRICT", "NO ACTION"):
            raise IntegrityError(
                f"cannot delete {table}: {len(children)} {rel['child']} row(s) "
                f"still reference it ({action})"
            )
        if action == "CASCADE":
            for _, _, crow in children:
                delete_row(
                    db, plan, rel["child"], _identity_match(plan, rel["child"], crow), session
                )
        elif action == "SET NULL":
            _update_rows(db, plan, rel["child"], match, unset=list(rel["columns"]), session=session)
        elif action == "SET DEFAULT":
            defaults = {c: _default_for(plan, rel["child"], c) for c in rel["columns"]}
            _update_rows(db, plan, rel["child"], match, set_=defaults, session=session)


def _apply_embedded_rules(
    db: Any, plan: dict[str, Any], table: str, node: Any, row: dict[str, Any], session: Any
) -> None:
    """Rows physically nested inside the row vanish with it: apply their parent rules first."""
    _apply_parent_rules(db, plan, table, row, session)
    for rel in plan["relationships"]:
        if rel["parent"] != table or not rel["embedded"]:
            continue
        child = plan["tables"][rel["child"]]
        if rel["on_delete"] in ("RESTRICT", "NO ACTION") and _elements(node, child["path"][-1]):
            raise IntegrityError(
                f"cannot delete {table}: it still contains {rel['child']} row(s) "
                f"({rel['on_delete']})"
            )
    for child, ct in plan["tables"].items():
        if ct["host"] != table:
            continue
        for elem, _holder in _elements(node, ct["path"][-1]):
            crow = _row_of(plan, child, elem, node)
            _apply_embedded_rules(db, plan, child, elem, crow, session)


def delete_row(
    db: Any, plan: dict[str, Any], table: str, match: dict[str, Any], session: Any = None
) -> int:
    """DELETE with SQL semantics: CASCADE / SET NULL / SET DEFAULT / RESTRICT are honoured."""
    rows = locate(db, plan, table, match, session)
    for doc, node, row in rows:
        _apply_embedded_rules(db, plan, table, node, row, session)
        _remove_row(db, plan, table, doc, row, session)
    return len(rows)


# ------------------------------------------------------------------------- insert
def _fill_defaults(
    db: Any, plan: dict[str, Any], table: str, values: dict[str, Any], session: Any
) -> dict[str, Any]:
    t = plan["tables"][table]
    out = dict(values)
    for col in t["columns"]:
        if out.get(col) is not None:
            continue
        spec = plan["defaults"].get(table, {}).get(col)
        if spec is not None:
            if spec["kind"] == "now":
                out[col] = datetime.now(UTC)
            else:
                out[col] = spec["value"]
                if isinstance(out[col], str) and t["columns"][col]["base"] in (
                    "DATE",
                    "TIMESTAMP",
                    "TIMESTAMPTZ",
                ):
                    out[col] = datetime.fromisoformat(out[col])
        elif col in t["identity"] and plan["options"]["preserve_integer_ids"]:
            out[col] = next_id(db, f"{table}.{col}", session)
    return out


def insert_row(
    db: Any, plan: dict[str, Any], table: str, values: dict[str, Any], session: Any = None
) -> Any:
    """INSERT with defaults, identity ids, foreign-key checks and in-array uniqueness checks."""
    t = plan["tables"][table]
    row = _fill_defaults(db, plan, table, values, session)
    for rel in plan["relationships"]:
        if rel["child"] != table or rel["embedded"]:
            continue
        vals = [row.get(c) for c in rel["columns"]]
        if all(v is not None for v in vals) and not exists(
            db, plan, rel["parent"], dict(zip(rel["ref_columns"], vals, strict=True)), session
        ):
            raise IntegrityError(
                f"{table}: no {rel['parent']} row with {rel['ref_columns']} = {vals}"
            )
    if t["kind"] == "root":
        doc = row_to_doc(plan, table, row)
        if "_id" not in doc and t["id_column"] is None:
            doc["_id"] = ObjectId()
        if t["id_column"] is not None and t["fields"][t["id_column"]] not in doc:
            doc["_id"] = ObjectId()
        result = db[t["collection"]].insert_one(doc, session=session)
        return result.inserted_id
    host = plan["tables"][t["host"]]
    for dropped in t["dropped"]:
        if row.get(dropped) is None:
            raise IntegrityError(f"{table}: {dropped} is required to locate the {t['host']} row")
    for key in [t["pk"], *plan["unique"].get(table, [])]:
        if key and all(row.get(c) is not None for c in key):
            same_host = {c: row[c] for c in t["dropped"]}
            if locate(db, plan, table, {**same_host, **{c: row[c] for c in key}}, session):
                raise IntegrityError(f"{table}: duplicate key {key} within its {t['host']} row")
    flt = {
        host["fields"][hc]: _bson(plan, table, dc, row[dc])
        for hc, dc in zip(t["host_columns"], t["dropped"], strict=True)
    }
    host_filter = {
        f"h.{host['fields'][hc]}": _bson(plan, table, dc, row[dc])
        for hc, dc in zip(t["host_columns"], t["dropped"], strict=True)
    }
    kwargs: dict[str, Any] = {}
    if host["kind"] == "root":
        target = t["path"][-1]
    else:
        host_path, filters = _positional(
            plan,
            t["host"],
            last="h" if host["kind"] in ("array", "ref_docs") else None,
            last_filter=host_filter,
        )
        target = f"{host_path}.{t['path'][-1]}"
        if filters:
            kwargs["array_filters"] = filters
        flt = {".".join([*host["path"], k]): v for k, v in flt.items()}
    if t["kind"] == "ref_scalars":
        update: dict[str, Any] = {
            "$addToSet": {target: _bson(plan, table, t["scalar_column"], row[t["scalar_column"]])}
        }
    elif t["kind"] == "object":
        update = {"$set": {target: row_to_doc(plan, table, row)}}
    else:
        update = {"$push": {target: row_to_doc(plan, table, row)}}
    result = db[t["collection"]].update_one(flt, update, session=session, **kwargs)
    if result.matched_count == 0:
        raise IntegrityError(
            f"{table}: no {t['host']} row matches {t['dropped']} = {[row[c] for c in t['dropped']]}"
        )
    return None


# ------------------------------------------------------------------------- update
def update_row(
    db: Any,
    plan: dict[str, Any],
    table: str,
    match: dict[str, Any],
    changes: dict[str, Any],
    session: Any = None,
) -> int:
    """UPDATE with ON UPDATE CASCADE / RESTRICT emulation for changed referenced keys."""
    t = plan["tables"][table]
    rows = locate(db, plan, table, match, session)
    for doc, _node, row in rows:
        for rel in plan["relationships"]:
            if rel["parent"] != table or rel["embedded"]:
                continue
            changed = [c for c in rel["ref_columns"] if c in changes and changes[c] != row.get(c)]
            if not changed:
                continue
            old = dict(zip(rel["columns"], [row.get(c) for c in rel["ref_columns"]], strict=True))
            children = locate(db, plan, rel["child"], old, session)
            if not children:
                continue
            if rel["on_update"] == "CASCADE":
                new = {
                    fc: changes.get(rc, row.get(rc))
                    for fc, rc in zip(rel["columns"], rel["ref_columns"], strict=True)
                }
                _update_rows(db, plan, rel["child"], old, set_=new, session=session)
            else:
                raise IntegrityError(
                    f"cannot update {table}: {rel['child']} rows reference the old key"
                )
        if (
            t["kind"] == "root"
            and t["id_column"] in changes
            and changes[t["id_column"]] != row.get(t["id_column"])
        ):
            new_row = {**row, **changes}
            db[t["collection"]].delete_one({"_id": doc["_id"]}, session=session)
            doc2 = {**doc, **row_to_doc(plan, table, new_row)}
            db[t["collection"]].insert_one(doc2, session=session)
        else:
            _update_rows(
                db, plan, table, _identity_match(plan, table, row), set_=changes, session=session
            )
    return len(rows)
