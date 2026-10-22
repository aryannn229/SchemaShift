"""Incremental migration compiler: two schema versions -> an ordered, classified migration plan.
Pure.

Pipeline: parse both versions (or apply ``ALTER`` statements to the old one) -> typed diff ->
re-plan with the
optimizer -> ordered steps with dependencies, reversibility and rollback -> guarantee diff.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot

from schemashift.codegen.collections import CollectionSpec, build_collections
from schemashift.codegen.indexes import IndexSpec, build_indexes
from schemashift.codegen.layout import TableLayout, build_layouts
from schemashift.codegen.predicate import Untranslatable, literal_value
from schemashift.codegen.values import to_py
from schemashift.evolve.alter import apply_alters
from schemashift.evolve.differ import diff_schemas
from schemashift.evolve.model import (
    ChangeOp,
    EvolutionPlan,
    GuaranteeDiff,
    MigrationStep,
    PlanChange,
    Reversibility,
    SchemaDiff,
    StepKind,
    VerdictChange,
)
from schemashift.models.schema import Column
from schemashift.pipeline import CompileOptions, CompileResult, compile_schema, compile_sql

RANK = {"SAFE": 0, "CHANGED": 1, "BROKEN": 2}
WIDENING = {
    ("SMALLINT", "INTEGER"),
    ("SMALLINT", "BIGINT"),
    ("INTEGER", "BIGINT"),
    ("INTEGER", "DECIMAL"),
    ("BIGINT", "DECIMAL"),
    ("FLOAT", "DOUBLE"),
    ("SMALLINT", "DECIMAL"),
}
CONVERT_TO = {
    "SMALLINT": "int",
    "INTEGER": "int",
    "BIGINT": "long",
    "FLOAT": "double",
    "DOUBLE": "double",
    "DECIMAL": "decimal",
    "TEXT": "string",
    "VARCHAR": "string",
    "CHAR": "string",
    "BOOLEAN": "bool",
    "DATE": "date",
    "TIMESTAMP": "date",
    "TIMESTAMPTZ": "date",
}


# ======================================================================== plan changes
def _embedded(decision: str | None) -> bool:
    return decision in ("EMBED", "REF_ARRAY")


def diff_plans(old: CompileResult, new: CompileResult) -> tuple[PlanChange, ...]:
    out: list[PlanChange] = []
    ids = sorted(set(old.plan.decisions) | set(new.plan.decisions))
    for rid in ids:
        a = old.plan.decisions.get(rid)
        b = new.plan.decisions.get(rid)
        da, db = (a.decision if a else None), (b.decision if b else None)
        if da == db:
            continue
        if a is None:
            reason = f"new relationship: {b.reason if b else ''}".strip()
        elif b is None:
            reason = "relationship no longer exists"
        else:
            reason = b.reason or "the optimizer's decision changed"
        out.append(
            PlanChange(
                relationship_id=rid,
                old=da,
                new=db,
                restructures=_embedded(da) != _embedded(db),
                reason=reason,
            )
        )
    return tuple(out)


# ======================================================================== guarantee diff
def _node_names(ops: tuple[ChangeOp, ...]) -> dict[str, str]:
    """Old name -> new name for confirmed renames (``table`` and ``table.column`` forms)."""
    out: dict[str, str] = {}
    for op in ops:
        if op.kind == "RENAME_TABLE":
            out[op.table] = str(op.detail["new"])
        elif op.kind == "RENAME_COLUMN" and op.column:
            out[f"{op.table}.{op.column}"] = f"{op.table}.{op.detail['new']}"
    return out


def _translate(node_id: str, names: dict[str, str]) -> str:
    for old, new in sorted(names.items(), key=lambda kv: -len(kv[0])):
        node_id = re.sub(rf"(?<![\w]){re.escape(old)}(?![\w])", new, node_id)
    return node_id


def _because(
    node_id: str, ops: tuple[ChangeOp, ...], plan_changes: tuple[PlanChange, ...]
) -> tuple[str, ...]:
    tokens = set(re.split(r"[^\w]+", node_id))
    found: list[str] = []
    for op in ops:
        cols = {op.column} if op.column else set()
        for key in ("columns", "new"):
            val = op.detail.get(key)
            cols |= (
                set(val) if isinstance(val, list) else ({val} if isinstance(val, str) else set())
            )
        if op.table in tokens and (
            not cols or cols & tokens or op.kind in {"ADD_TABLE", "DROP_TABLE"}
        ):
            found.append(op.description)
    for pc in plan_changes:
        if any(
            t and t in pc.relationship_id for t in tokens if t not in {"fk", "m2n"} and len(t) > 2
        ):
            found.append(f"{pc.relationship_id}: {pc.old} -> {pc.new}")
    return tuple(dict.fromkeys(found))[:4]


def diff_guarantees(
    old: CompileResult,
    new: CompileResult,
    ops: tuple[ChangeOp, ...],
    plan_changes: tuple[PlanChange, ...],
) -> GuaranteeDiff:
    names = _node_names(ops)
    before = {_translate(v.node_id, names): v for v in old.equivalence.final.verdicts}
    after = {v.node_id: v for v in new.equivalence.final.verdicts}
    changes: list[VerdictChange] = []
    for node_id in sorted(set(before) | set(after)):
        a, b = before.get(node_id), after.get(node_id)
        if a and b:
            if a.status == b.status:
                continue
            worse = RANK[b.status] > RANK[a.status]
            changes.append(
                VerdictChange(
                    node_id=node_id,
                    rule_id=b.rule_id,
                    old=a.status,
                    new=b.status,
                    direction="worse" if worse else "better",
                    because=_because(node_id, ops, plan_changes),
                    reason=b.reason,
                )
            )
        elif b:
            changes.append(
                VerdictChange(
                    node_id=node_id,
                    rule_id=b.rule_id,
                    old=None,
                    new=b.status,
                    direction="added",
                    because=_because(node_id, ops, plan_changes),
                    reason=b.reason,
                )
            )
        elif a:
            changes.append(
                VerdictChange(
                    node_id=node_id,
                    rule_id=a.rule_id,
                    old=a.status,
                    new=None,
                    direction="removed",
                    because=_because(node_id, ops, plan_changes),
                    reason=f"the guarantee no longer exists in the new schema (was: {a.reason})",
                )
            )
    return GuaranteeDiff(
        old_overall=old.overall_verdict,
        new_overall=new.overall_verdict,
        changes=tuple(changes),
        regressions=sum(c.direction == "worse" for c in changes),
        improvements=sum(c.direction == "better" for c in changes),
    )


# ======================================================================== steps
def _opts(spec: CollectionSpec) -> dict[str, Any]:
    options: dict[str, Any] = {
        "validator": spec.validator,
        "validationLevel": spec.validation_level,
        "validationAction": spec.validation_action,
    }
    if spec.collation:
        options["collation"] = spec.collation
    return options


def _index_opts(ix: IndexSpec) -> dict[str, Any]:
    out: dict[str, Any] = {"name": ix.name}
    if ix.unique:
        out["unique"] = True
    if ix.partial_filter:
        out["partialFilterExpression"] = ix.partial_filter
    return out


def _index_id(ix: IndexSpec) -> tuple[Any, ...]:
    return (ix.collection, ix.name, ix.keys, ix.unique, repr(ix.partial_filter))


def _segs(layouts: dict[str, TableLayout], table: str) -> list[tuple[str, str]]:
    """Embedding steps from the collection root down to the table's own object."""
    out: list[tuple[str, str]] = []
    layout = layouts[table]
    chain: list[TableLayout] = []
    while layout.host is not None and layout.host in layouts:
        chain.append(layout)
        layout = layouts[layout.host]
    for lay in reversed(chain):
        out.append((lay.path[-1], "arr" if lay.is_array else "obj"))
    return out


def _default_value(col: Column) -> Any:
    """Python value of a literal default, or ``Untranslatable`` for expressions such as now()."""
    if col.default is None or col.default.kind != "literal":
        raise Untranslatable(col.default.sql if col.default else "")
    node = sqlglot.parse_one(col.default.sql, dialect="postgres")
    return literal_value(node, col.sql_type, True)


def _call(fn: str, *args: Any) -> str:
    """Source of ``evolve_runtime.fn(db, ...)``; the first argument is always the ``db``
    variable."""
    return f"evolve_runtime.{fn}(db, " + ", ".join(to_py(a) for a in args[1:]) + ")"


class _Steps:
    def __init__(self) -> None:
        self.steps: list[MigrationStep] = []
        self.by_collection: dict[str, list[str]] = {}

    def add(
        self,
        kind: StepKind,
        collection: str | None,
        description: str,
        *,
        code: str = "",
        rollback: str | None = None,
        reversibility: Reversibility = "REVERSIBLE",
        reason: str = "",
        depends_on: tuple[str, ...] = (),
        saved_data: str | None = None,
        recovery: str | None = None,
        manual: bool = False,
        changes: tuple[str, ...] = (),
    ) -> str:
        sid = f"s{len(self.steps) + 1}"
        self.steps.append(
            MigrationStep(
                id=sid,
                kind=kind,
                collection=collection,
                description=description,
                depends_on=depends_on,
                reversibility=reversibility,
                reason=reason,
                code=code,
                rollback=None if reversibility == "IRREVERSIBLE" else rollback,
                saved_data=saved_data,
                recovery=recovery,
                manual=manual,
                changes=changes,
            )
        )
        if collection:
            self.by_collection.setdefault(collection, []).append(sid)
        return sid


def _manual(
    st: _Steps, collection: str | None, what: str, why: str, deps: tuple[str, ...] = ()
) -> str:
    return st.add(
        "MANUAL",
        collection,
        what,
        reversibility="IRREVERSIBLE",
        reason=why,
        recovery=(
            "Take a snapshot first (`mongodump --db <db> --collection "
            f"{collection or '<collection>'}` or copy it with an aggregation `$out`), perform the "
            "change by hand, verify with `schemashift verify`, and keep the snapshot until then."
        ),
        manual=True,
        depends_on=deps,
        changes=(what,),
    )


def build_steps(
    old: CompileResult, new: CompileResult, diff: SchemaDiff, options: CompileOptions
) -> tuple[MigrationStep, ...]:
    l1 = build_layouts(old.graph, old.plan)
    l2 = build_layouts(new.graph, new.plan)
    c1 = {
        c.name: c
        for c in build_collections(
            old.schema_, l1, options.uuid_as, options.string_collation
        ).collections
    }
    c2 = {
        c.name: c
        for c in build_collections(
            new.schema_, l2, options.uuid_as, options.string_collation
        ).collections
    }
    i1 = {_index_id(i): i for i in build_indexes(old.schema_, old.graph, l1)}
    i2 = {_index_id(i): i for i in build_indexes(new.schema_, new.graph, l2)}
    tmap = {op.table: str(op.detail["new"]) for op in diff.ops if op.kind == "RENAME_TABLE"}
    cmap = {
        (op.table, op.column): str(op.detail["new"])
        for op in diff.ops
        if op.kind == "RENAME_COLUMN"
    }
    ops_by_table: dict[str, list[ChangeOp]] = {}
    for op in diff.ops:
        ops_by_table.setdefault(op.table, []).append(op)
    st = _Steps()
    touched: set[str] = set()  # collections whose validator is relaxed during the migration
    data_steps: dict[str, list[str]] = {}

    def coll_after(c: str) -> str:
        return tmap.get(c, c)

    # ---- collections that appear (new tables, and tables that leave their host document)
    for coll in sorted(c2):
        if coll in c1 or coll in set(tmap.values()):
            continue
        sid = st.add(
            "CREATE_COLLECTION",
            coll,
            f"create collection {coll}",
            code=_call("set_validator", "db", coll, _opts(c2[coll])),
            rollback=_call("archive_collection", "db", coll),
            reason="a new collection starts empty; rolling back renames it to _archive_<name>",
            changes=(f"add collection for table {c2[coll].table}",),
        )
        data_steps.setdefault(coll, []).append(sid)

    # ---- tables that disappear / appear / are renamed

    def relax(coll: str) -> str | None:
        if coll in c1 and coll_after(coll) in c2 and coll not in touched:
            touched.add(coll)
            return st.add(
                "UPDATE_VALIDATOR",
                coll,
                f"relax validation on {coll} while its documents are rewritten",
                code=_call("relax_validator", "db", coll),
                rollback=_call("set_validator", "db", coll, _opts(c1[coll])),
                reason="validationLevel is switched back on by its final validator step",
            )
        return None

    def add_data(coll: str, sid: str) -> None:
        data_steps.setdefault(coll, []).append(sid)

    for table in sorted(l1):
        old_layout = l1[table]
        new_name = tmap.get(table, table)
        # --- dropped tables
        if new_name not in l2:
            if old_layout.is_root:
                sid = st.add(
                    "ARCHIVE_COLLECTION",
                    old_layout.collection,
                    f"archive collection {old_layout.collection} (table {table} was dropped)",
                    code=_call("archive_collection", "db", old_layout.collection),
                    rollback=_call("restore_collection", "db", old_layout.collection),
                    reversibility="REVERSIBLE_WITH_SAVED_DATA",
                    reason="the collection is renamed to _archive_<name>, not deleted",
                    saved_data=f"_archive_{old_layout.collection}",
                    changes=(f"drop table {table}",),
                )
            else:
                coll = old_layout.collection
                relax(coll)
                top = old_layout.path[0]
                st.add(
                    "UNSET_FIELD",
                    coll,
                    f"remove the embedded {'/'.join(old_layout.path)} of dropped table {table} "
                    f"from {coll}",
                    code=(
                        _call("backup_top", "db", coll, top, f"s{len(st.steps) + 1}")
                        + "\n"
                        + _call(
                            "unset_field", "db", coll, _segs(l1, table)[:-1], old_layout.path[-1]
                        )
                    ),
                    rollback=_call("restore_top", "db", coll, top, f"s{len(st.steps) + 1}"),
                    reversibility="REVERSIBLE_WITH_SAVED_DATA",
                    reason="the embedded values are copied to _evolve_backup before removal",
                    saved_data="_evolve_backup",
                    changes=(f"drop table {table}",),
                )
            continue
        new_layout = l2[new_name]
        # --- layout changes (restructuring)
        if old_layout.is_root and new_layout.is_root:
            if new_name != table:
                rid = st.add(
                    "RENAME_FIELD",
                    old_layout.collection,
                    f"rename collection {table} to {new_name}",
                    code=_call("rename_collection", "db", table, new_name),
                    rollback=_call("rename_collection", "db", new_name, table),
                    reason="a collection rename keeps documents and indexes",
                    changes=(f"rename table {table} to {new_name}",),
                )
                add_data(new_name, rid)
        elif (old_layout.kind, old_layout.path, old_layout.collection) != (
            new_layout.kind,
            new_layout.path,
            coll_after(old_layout.collection),
        ):
            _restructure(
                st, old, new, l1, l2, old_layout, new_layout, touched, data_steps, relax, add_data
            )
            continue
        elif new_name != table:
            _manual(
                st,
                old_layout.collection,
                f"rename embedded table {table} to {new_name}",
                "renaming an embedded field is not automated",
            )
        # --- column level changes in place
        for op in ops_by_table.get(table, []):
            _column_op(st, op, old_layout, new_layout, l1, l2, old, new, cmap, relax, add_data)

    # ---- indexes
    for key, ix in i1.items():
        if key in i2:
            continue
        coll = ix.collection
        if coll not in c2 and coll_after(coll) not in c2:
            continue  # archived with the collection
        sid = st.add(
            "DROP_INDEX",
            coll,
            f"drop index {ix.name} on {coll}",
            code=_call("drop_index", "db", coll, ix.name),
            rollback=_call("create_index", "db", coll, list(ix.keys), _index_opts(ix)),
            reason="an index can be recreated from its definition",
        )
        add_data(coll, sid)
    # ---- final validators
    final_ids: dict[str, str] = {}
    for coll, spec in sorted(c2.items()):
        old_name = next((o for o, n in tmap.items() if n == coll), coll)
        before = c1.get(old_name)
        if before is None:
            continue
        if old_name in touched or _opts(before) != _opts(spec):
            final_ids[coll] = st.add(
                "UPDATE_VALIDATOR",
                coll,
                f"install the new validator on {coll}",
                code=_call("set_validator", "db", coll, _opts(spec)),
                # while the documents are being rewritten back, validation stays off; the relax
                # step's own
                # rollback (which runs last) restores the previous validator
                rollback=(
                    _call("relax_validator", "db", coll)
                    if old_name in touched
                    else _call("set_validator", "db", coll, _opts(before))
                ),
                reason="collMod can restore the previous validator document",
                depends_on=tuple(data_steps.get(old_name, []) + data_steps.get(coll, [])),
            )
    for key, ix in i2.items():
        if key in i1:
            continue
        deps = tuple(data_steps.get(ix.collection, []))
        st.add(
            "CREATE_INDEX",
            ix.collection,
            f"create {'unique ' if ix.unique else ''}index {ix.name} on {ix.collection}",
            code=_call("create_index", "db", ix.collection, list(ix.keys), _index_opts(ix)),
            rollback=_call("drop_index", "db", ix.collection, ix.name),
            reason="an index can be dropped again"
            + (
                "; a unique index fails when existing documents contain duplicates"
                if ix.unique
                else ""
            ),
            depends_on=deps,
        )
    # step ids of data steps are only known after they were added: backfill dependencies for
    # validators
    return tuple(_with_dependencies(st.steps, data_steps))


def _with_dependencies(
    steps: list[MigrationStep], data_steps: dict[str, list[str]]
) -> list[MigrationStep]:
    out = []
    for s in steps:
        deps = list(s.depends_on)
        if s.kind == "UPDATE_VALIDATOR" and s.collection and s.description.startswith("install"):
            deps += [d for d in data_steps.get(s.collection, []) if d != s.id]
        out.append(s.model_copy(update={"depends_on": tuple(dict.fromkeys(deps))}))
    return out


def _column_op(
    st: _Steps,
    op: ChangeOp,
    old_layout: TableLayout,
    new_layout: TableLayout,
    l1: dict[str, TableLayout],
    l2: dict[str, TableLayout],
    old: CompileResult,
    new: CompileResult,
    cmap: dict[tuple[str, str | None], str],
    relax: Any,
    add_data: Any,
) -> None:
    coll = old_layout.collection
    table = op.table
    segs = _segs(l1, table)
    top = old_layout.path[0] if old_layout.path else None
    new_table = new.schema_.tables.get(table)
    field: str | None
    if op.kind == "ADD_COLUMN" and op.column and new_table is not None:
        col = new_table.column(op.column)
        if col is None or op.column not in new_layout.fields:
            return
        field = new_layout.fields[op.column]
        if col.default is None:
            return  # an absent field is NULL: nothing to write
        try:
            value = _default_value(col)
        except Untranslatable:
            _manual(
                st,
                coll,
                op.description,
                f"the default {col.default.sql} is not a literal; backfill by hand",
            )
            return
        pre = relax(coll)
        sid = st.add(
            "ADD_FIELD",
            coll,
            f"backfill {coll}.{'.'.join([*(s for s, _ in segs), field])} with its default",
            code=_call("set_default", "db", coll, segs, field, value),
            rollback=_call("unset_field", "db", coll, segs, field),
            reason="the field did not exist before, so removing it restores the old documents",
            depends_on=(pre,) if pre else (),
            changes=(op.description,),
        )
        add_data(coll, sid)
    elif op.kind == "DROP_COLUMN" and op.column:
        field = old_layout.fields.get(op.column)
        if field is None:
            return
        if field == "_id":
            _manual(st, coll, op.description, "the dropped column is the document _id")
            return
        pre = relax(coll)
        step = f"s{len(st.steps) + 1}"
        sid = st.add(
            "UNSET_FIELD",
            coll,
            f"remove field {field} from {coll}",
            code=(
                _call("backup_top", "db", coll, top or field, step)
                + "\n"
                + _call("unset_field", "db", coll, segs, field)
            ),
            rollback=_call("restore_top", "db", coll, top or field, step),
            reversibility="REVERSIBLE_WITH_SAVED_DATA",
            reason="the field values are copied to _evolve_backup before they are removed",
            saved_data="_evolve_backup",
            depends_on=(pre,) if pre else (),
            changes=(op.description,),
        )
        add_data(coll, sid)
    elif op.kind == "RENAME_COLUMN" and op.column:
        a = old_layout.fields.get(op.column)
        b = new_layout.fields.get(str(op.detail["new"]))
        if a is None or b is None:
            return
        if a == "_id" or b == "_id":
            _manual(st, coll, op.description, "the renamed column is the document _id")
            return
        pre = relax(coll)
        sid = st.add(
            "RENAME_FIELD",
            coll,
            f"rename field {a} to {b} in {coll}",
            code=_call("rename_field", "db", coll, segs, a, b),
            rollback=_call("rename_field", "db", coll, segs, b, a),
            reason="renaming back restores the old field names",
            depends_on=(pre,) if pre else (),
            changes=(op.description,),
        )
        add_data(coll, sid)
    elif op.kind == "RETYPE_COLUMN" and op.column and new_table is not None:
        col = new_table.column(op.column)
        before = old.schema_.tables[table].column(op.column)
        field = new_layout.fields.get(op.column)
        if col is None or before is None or field is None:
            return
        if field == "_id":
            _manual(st, coll, op.description, "the retyped column is the document _id")
            return
        target = CONVERT_TO.get(col.sql_type.base)
        if target is None or col.sql_type.is_array or before.sql_type.is_array:
            _manual(st, coll, op.description, f"no automatic conversion to {col.sql_type.base}")
            return
        pre = relax(coll)
        step = f"s{len(st.steps) + 1}"
        widening = (before.sql_type.base, col.sql_type.base) in WIDENING
        back = CONVERT_TO.get(before.sql_type.base)
        if widening and back:
            sid = st.add(
                "RETYPE_FIELD",
                coll,
                f"convert {coll}.{field} from {before.sql_type.base} to {col.sql_type.base}",
                code=_call("convert_field", "db", coll, segs, field, target),
                rollback=_call("convert_field", "db", coll, segs, field, back),
                reason="a widening numeric conversion loses nothing, so it can be converted back",
                depends_on=(pre,) if pre else (),
                changes=(op.description,),
            )
        else:
            sid = st.add(
                "RETYPE_FIELD",
                coll,
                f"convert {coll}.{field} from {before.sql_type.base} to {col.sql_type.base}",
                code=(
                    _call("backup_top", "db", coll, top or field, step)
                    + "\n"
                    + _call("convert_field", "db", coll, segs, field, target)
                ),
                rollback=_call("restore_top", "db", coll, top or field, step),
                reversibility="REVERSIBLE_WITH_SAVED_DATA",
                reason="the conversion can lose information, so the originals are saved first",
                saved_data="_evolve_backup",
                depends_on=(pre,) if pre else (),
                changes=(op.description,),
            )
        add_data(coll, sid)
    elif op.kind == "CHANGE_PRIMARY_KEY":
        _manual(
            st,
            coll,
            op.description,
            "changing the primary key re-keys every document and every reference to it",
        )


def _restructure(
    st: _Steps,
    old: CompileResult,
    new: CompileResult,
    l1: dict[str, TableLayout],
    l2: dict[str, TableLayout],
    a: TableLayout,
    b: TableLayout,
    touched: set[str],
    data_steps: dict[str, list[str]],
    relax: Any,
    add_data: Any,
) -> None:
    table = a.table
    why = f"{table} moves from {a.kind} in {a.collection} to {b.kind} in {b.collection}"
    t = old.schema_.tables[table]
    pk = t.primary_key.columns if t.primary_key else ()
    # embedded -> its own collection
    if a.kind in ("array", "object") and b.is_root and len(a.path) == 1 and len(pk) == 1:
        host = l1[a.host or ""]
        fk = a.dropped_columns
        if host.is_root and host.id_column and len(fk) == 1 and a.host_columns == (host.id_column,):
            pre = relax(a.collection)
            step = f"s{len(st.steps) + 1}"
            sid = st.add(
                "EXTRACT_EMBEDDED",
                b.collection,
                f"move {a.collection}.{a.path[0]} into its own collection {b.collection}",
                code=_call(
                    "extract_embedded",
                    "db",
                    a.collection,
                    a.path[0],
                    b.collection,
                    fk[0],
                    "_id",
                    pk[0],
                    "arr" if a.kind == "array" else "obj",
                    step,
                ),
                rollback=(
                    _call(
                        "embed_collection",
                        "db",
                        a.collection,
                        a.path[0],
                        b.collection,
                        fk[0],
                        "arr" if a.kind == "array" else "obj",
                        "_id",
                        "_id",
                        pk[0],
                    )
                ),
                reversibility="REVERSIBLE_WITH_SAVED_DATA",
                reason="the embedded field is saved first; the collection can be folded back",
                saved_data="_evolve_backup",
                depends_on=((pre,) if pre else ()) + tuple(data_steps.get(b.collection, [])),
                changes=(why,),
            )
            add_data(b.collection, sid)
            add_data(a.collection, sid)
            return
    # its own collection -> embedded
    if a.is_root and b.kind in ("array", "object") and len(b.path) == 1 and len(pk) == 1:
        host = l2[b.host or ""]
        fk = b.dropped_columns
        if host.is_root and host.id_column and len(fk) == 1 and b.host_columns == (host.id_column,):
            pre = relax(host.collection)
            sid = st.add(
                "EMBED_COLLECTION",
                host.collection,
                f"fold collection {a.collection} into {host.collection}.{b.path[0]}",
                code=_call(
                    "embed_collection",
                    "db",
                    host.collection,
                    b.path[0],
                    a.collection,
                    fk[0],
                    "arr" if b.kind == "array" else "obj",
                    "_id",
                    "_id",
                    pk[0],
                ),
                rollback=(
                    _call(
                        "extract_embedded",
                        "db",
                        host.collection,
                        b.path[0],
                        a.collection,
                        fk[0],
                        "_id",
                        pk[0],
                        "arr" if b.kind == "array" else "obj",
                    )
                    + "\n"
                    + _call("restore_collection", "db", a.collection)
                ),
                reversibility="REVERSIBLE_WITH_SAVED_DATA",
                reason="the source collection is archived (renamed), not deleted",
                saved_data=f"_archive_{a.collection}",
                depends_on=(pre,) if pre else (),
                changes=(why,),
            )
            add_data(host.collection, sid)
            return
    _manual(st, a.collection, why, "this combination of layouts has no automatic restructuring")


def evolve(
    old_sql: str,
    new_sql: str = "",
    *,
    alter_sql: str = "",
    queries_sql: str = "",
    options: CompileOptions | None = None,
    confirmed_renames: dict[str, str] | None = None,
    new_options: CompileOptions | None = None,
) -> tuple[EvolutionPlan, CompileResult, CompileResult]:
    """Compile both versions and describe how to get from one to the other.

    Returns the plan together with both compile results (the verification harness needs them).
    ``new_options`` lets the new version use other options (for example different placement
    overrides).
    """
    options = options or CompileOptions()
    old = compile_sql(old_sql, queries_sql, "", options)
    renames = dict(confirmed_renames or {})
    warnings: list[str] = []
    if alter_sql.strip():
        schema2, w, explicit = apply_alters(old.schema_, alter_sql)
        warnings += w
        renames.update(explicit)
        new = compile_schema(schema2, queries_sql, "", new_options or options)
    else:
        new = compile_sql(new_sql, queries_sql, "", new_options or options)
    diff = diff_schemas(old.schema_, new.schema_, renames)
    plan_changes = diff_plans(old, new)
    steps = build_steps(old, new, diff, new_options or options)
    guarantees = diff_guarantees(old, new, diff.ops, plan_changes)
    plan = EvolutionPlan(
        diff=diff,
        plan_changes=plan_changes,
        steps=steps,
        guarantees=guarantees,
        warnings=tuple(warnings),
    )
    return plan, old, new


def plan_evolution(
    old_sql: str,
    new_sql: str = "",
    *,
    alter_sql: str = "",
    queries_sql: str = "",
    options: CompileOptions | None = None,
    confirmed_renames: dict[str, str] | None = None,
) -> EvolutionPlan:
    """The migration plan from ``old_sql`` to ``new_sql`` (or to the result of ``alter_sql``)."""
    return evolve(
        old_sql,
        new_sql,
        alter_sql=alter_sql,
        queries_sql=queries_sql,
        options=options,
        confirmed_renames=confirmed_renames,
    )[0]
