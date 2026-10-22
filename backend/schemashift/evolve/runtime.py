"""Runtime of the generated incremental migration (embedded verbatim into ``evolve.py``).

Every function takes a pymongo ``Database``. ``segs`` describes where a field lives inside a
collection's
documents: a list of ``(name, "obj" | "arr")`` embedding steps from the document root (empty for
fields
directly on the root document). Backups for steps that destroy data are kept in ``_evolve_backup``
so that a
rollback can restore them; nothing here deletes a collection (dropped tables are renamed to
``_archive_*``).
"""

from __future__ import annotations

from typing import Any

BACKUP = "_evolve_backup"
ARCHIVE_PREFIX = "_archive_"
CONVERT_TYPES = {
    "int": "int",
    "long": "long",
    "double": "double",
    "decimal": "decimal",
    "string": "string",
    "bool": "bool",
    "date": "date",
}


# ------------------------------------------------------------------------------ path updates
def _rewrite(segs: list[tuple[str, str]], doc: str, leaf: Any, depth: int = 0) -> Any:
    """Expression for the new value of ``segs[0]`` inside ``doc`` after ``leaf`` ran on the target
    object.

    ``leaf(obj_expr)`` returns the expression for the changed object. Arrays are mapped, missing
    parents are
    left missing.
    """
    name, kind = segs[0]
    field = f"{doc}.{name}"
    last = len(segs) == 1
    var = f"e{depth}"
    if kind == "arr":
        inner = (
            leaf(f"$${var}")
            if last
            else _merge(f"$${var}", segs[1], _rewrite(segs[1:], f"$${var}", leaf, depth + 1))
        )
        return {
            "$cond": [
                {"$isArray": field},
                {"$map": {"input": field, "as": var, "in": inner}},
                "$$REMOVE",
            ]
        }
    inner = (
        leaf(field) if last else _merge(field, segs[1], _rewrite(segs[1:], field, leaf, depth + 1))
    )
    return {"$cond": [{"$eq": [{"$type": field}, "object"]}, inner, "$$REMOVE"]}


def _merge(obj: str, nxt: tuple[str, str], value: Any) -> Any:
    return {"$mergeObjects": [obj, {nxt[0]: value}]}


def update_in_path(
    db: Any, coll: str, segs: list[tuple[str, str]], leaf: Any, match: dict[str, Any] | None = None
) -> int:
    """Apply ``leaf`` to the object that ``segs`` points to in every document (an update
    pipeline)."""
    if not segs:
        result = db[coll].update_many(match or {}, [{"$replaceWith": leaf("$$ROOT")}])
        return int(result.modified_count)
    top = segs[0][0]
    expr = _rewrite(segs, "$$ROOT", leaf)  # the first segment is a field of the root document
    result = db[coll].update_many(match or {}, [{"$set": {top: expr}}])
    return int(result.modified_count)


def set_default(db: Any, coll: str, segs: list[tuple[str, str]], field: str, value: Any) -> int:
    """Fill ``field`` where it is absent or null (an added column with a default)."""

    def leaf(obj: str) -> Any:
        return {
            "$cond": [
                {"$gt": [f"{obj}.{field}", None]},
                obj,
                {"$mergeObjects": [obj, {field: {"$literal": value}}]},
            ]
        }

    return update_in_path(db, coll, segs, leaf)


def unset_field(db: Any, coll: str, segs: list[tuple[str, str]], field: str) -> int:
    def leaf(obj: str) -> Any:
        return {"$unsetField": {"field": field, "input": obj}}

    return update_in_path(db, coll, segs, leaf)


def rename_field(db: Any, coll: str, segs: list[tuple[str, str]], old: str, new: str) -> int:
    """Rename a field. Documents where ``old`` is absent are untouched."""

    def leaf(obj: str) -> Any:
        moved = {
            "$mergeObjects": [
                {"$unsetField": {"field": old, "input": obj}},
                {new: f"{obj}.{old}"},
            ]
        }
        return {"$cond": [{"$gt": [f"{obj}.{old}", None]}, moved, obj]}

    return update_in_path(db, coll, segs, leaf)


def convert_field(
    db: Any, coll: str, segs: list[tuple[str, str]], field: str, to: str, on_error: str = "keep"
) -> int:
    """Convert a field with ``$convert`` (``on_error``: ``keep`` the old value, or ``null``)."""
    target = CONVERT_TYPES[to]

    def leaf(obj: str) -> Any:
        conv = {
            "$convert": {
                "input": f"{obj}.{field}",
                "to": target,
                "onError": f"{obj}.{field}" if on_error == "keep" else None,
                "onNull": None,
            }
        }
        return {
            "$cond": [
                {"$gt": [f"{obj}.{field}", None]},
                {"$mergeObjects": [obj, {field: conv}]},
                obj,
            ]
        }

    return update_in_path(db, coll, segs, leaf)


# ------------------------------------------------------------------------------ backups
def backup_top(db: Any, coll: str, top: str, step: str) -> int:
    """Copy the top-level field ``top`` of every document that has it into the backup collection."""
    db[BACKUP].delete_many({"step": step, "coll": coll, "top": top})
    n = 0
    batch: list[dict[str, Any]] = []
    for doc in db[coll].find({top: {"$exists": True}}, {top: 1}):
        batch.append(
            {"step": step, "coll": coll, "top": top, "doc_id": doc["_id"], "value": doc[top]}
        )
        if len(batch) >= 500:
            db[BACKUP].insert_many(batch)
            n += len(batch)
            batch = []
    if batch:
        db[BACKUP].insert_many(batch)
        n += len(batch)
    return n


def restore_top(db: Any, coll: str, top: str, step: str) -> int:
    n = 0
    for row in db[BACKUP].find({"step": step, "coll": coll, "top": top}):
        db[coll].update_one({"_id": row["doc_id"]}, {"$set": {top: row["value"]}})
        n += 1
    db[BACKUP].delete_many({"step": step, "coll": coll, "top": top})
    return n


# ------------------------------------------------------------------------------ collections
def archive_collection(db: Any, coll: str) -> None:
    """Dropped tables are renamed, never deleted: the data stays available for a rollback."""
    if coll in db.list_collection_names():
        db[coll].rename(ARCHIVE_PREFIX + coll, dropTarget=True)


def restore_collection(db: Any, coll: str) -> None:
    if ARCHIVE_PREFIX + coll in db.list_collection_names():
        db[ARCHIVE_PREFIX + coll].rename(coll, dropTarget=True)


def rename_collection(db: Any, old: str, new: str) -> None:
    if old in db.list_collection_names():
        db[old].rename(new)


def set_validator(db: Any, coll: str, options: dict[str, Any]) -> None:
    """collMod; creates the collection first when it does not exist."""
    if coll not in db.list_collection_names():
        db.create_collection(coll, **options)
    else:
        db.command("collMod", coll, **{k: v for k, v in options.items() if k != "collation"})


def relax_validator(db: Any, coll: str) -> None:
    if coll in db.list_collection_names():
        db.command("collMod", coll, validationLevel="off")


# ------------------------------------------------------------------------------ restructuring
def extract_embedded(
    db: Any,
    host: str,
    field: str,
    target: str,
    fk_field: str,
    host_key: str = "_id",
    child_id: str = "_id",
    kind: str = "arr",
    backup_step: str | None = None,
) -> int:
    """Move the sub-documents in ``host.field`` into their own collection ``target``.

    Each child gets the host key back in ``fk_field`` (the column the embedding had dropped) and
    keeps its
    own id. The embedded field is unset afterwards. ``backup_step`` saves the embedded field first.
    """
    if backup_step:
        backup_top(db, host, field, backup_step)
    moved = 0
    batch: list[dict[str, Any]] = []
    for doc in db[host].find({field: {"$exists": True}}, {field: 1, host_key: 1}):
        items = doc[field] if kind == "arr" else [doc[field]]
        for item in items or []:
            child = dict(item)
            child[fk_field] = doc[host_key]
            if child_id != "_id" and child_id in child:
                child["_id"] = child.pop(child_id)
            batch.append(child)
        if len(batch) >= 500:
            db[target].insert_many(batch)
            moved += len(batch)
            batch = []
    if batch:
        db[target].insert_many(batch)
        moved += len(batch)
    db[host].update_many({field: {"$exists": True}}, {"$unset": {field: ""}})
    return moved


def embed_collection(
    db: Any,
    host: str,
    field: str,
    source: str,
    fk_field: str,
    kind: str = "arr",
    order_by: str = "_id",
    host_key: str = "_id",
    pk_field: str | None = None,
) -> int:
    """Fold collection ``source`` into ``host.field`` (the inverse of ``extract_embedded``).

    The children are archived (renamed to ``_archive_<source>``), so nothing is lost.
    """
    folded = 0
    pipeline: list[dict[str, Any]] = [
        {"$sort": {order_by: 1}},
        {"$group": {"_id": f"${fk_field}", "items": {"$push": "$$ROOT"}}},
    ]
    for group in db[source].aggregate(pipeline, allowDiskUse=True):
        items = []
        for item in group["items"]:
            child = {k: v for k, v in item.items() if k != fk_field}
            if pk_field and pk_field != "_id" and "_id" in child:
                child[pk_field] = child.pop("_id")
            items.append(child)
        value: Any = items if kind == "arr" else (items[0] if items else None)
        if value is None:
            continue
        db[host].update_one({host_key: group["_id"]}, {"$set": {field: value}})
        folded += len(items)
    archive_collection(db, source)
    return folded


def create_index(db: Any, coll: str, keys: list[tuple[str, int]], options: dict[str, Any]) -> None:
    db[coll].create_index(keys, **options)


def drop_index(db: Any, coll: str, name: str) -> None:
    try:
        db[coll].drop_index(name)
    except Exception:  # noqa: BLE001 - already gone
        pass
