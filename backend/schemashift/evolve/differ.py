"""Pure schema differ: two parsed schemas -> typed change operations.

Renames are detected heuristically but never assumed: a dropped and an added column of the same
type are
reported as a ``RenameCandidate``. Only candidates the caller confirms (``confirmed_renames``)
become
``RENAME_*`` operations; unconfirmed ones stay a drop plus an add, which is what loses data.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from schemashift.evolve.model import ChangeOp, RenameCandidate, SchemaDiff
from schemashift.models.schema import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Schema,
    Table,
    UniqueConstraint,
)

MIN_RENAME_SCORE = 0.5


def _type_sig(c: Column) -> tuple[Any, ...]:
    t = c.sql_type
    return (t.base, t.length, t.precision, t.scale, t.is_array, t.enum_name)


def _type_text(c: Column) -> str:
    t = c.sql_type
    extra = f"({t.length})" if t.length else f"({t.precision},{t.scale})" if t.precision else ""
    return f"{t.base}{extra}{'[]' if t.is_array else ''}"


def _similar(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a, b).ratio(), 3)


def _fk_key(fk: ForeignKey) -> tuple[Any, ...]:
    return (fk.columns, fk.ref_table, fk.ref_columns, fk.on_delete, fk.on_update)


def _fk_text(fk: ForeignKey) -> str:
    cols, refs = ", ".join(fk.columns), ", ".join(fk.ref_columns)
    return f"({cols}) -> {fk.ref_table}({refs}) ON DELETE {fk.on_delete}"


def _unique_key(u: UniqueConstraint) -> tuple[str, ...]:
    return u.columns


def _index_key(i: Index) -> tuple[Any, ...]:
    return (i.columns, i.unique, i.where_sql)


def _rename_pairs(dropped: list[Column], added: list[Column], table: str) -> list[RenameCandidate]:
    """Pair dropped and added columns of the same type by name similarity (best first)."""
    out: list[RenameCandidate] = []
    used_old: set[str] = set()
    used_new: set[str] = set()
    scored = sorted(
        (
            (_similar(d.name, a.name), d, a)
            for d in dropped
            for a in added
            if _type_sig(d) == _type_sig(a)
        ),
        key=lambda x: (-x[0], x[1].name, x[2].name),
    )
    only_pair = len(dropped) == 1 and len(added) == 1
    for score, d, a in scored:
        if d.name in used_old or a.name in used_new:
            continue
        if score < MIN_RENAME_SCORE and not only_pair:
            continue
        used_old.add(d.name)
        used_new.add(a.name)
        out.append(RenameCandidate(kind="column", table=table, old=d.name, new=a.name, score=score))
    return out


def diff_schemas(
    old: Schema, new: Schema, confirmed_renames: dict[str, str] | None = None
) -> SchemaDiff:
    """Typed differences. ``confirmed_renames``: ``{"table": "new_table", "table.col":
    "new_col"}``."""
    confirmed = confirmed_renames or {}
    ops: list[ChangeOp] = []
    candidates: list[RenameCandidate] = []
    warnings: list[str] = []

    # ---- tables
    dropped_t = sorted(set(old.tables) - set(new.tables))
    added_t = sorted(set(new.tables) - set(old.tables))
    table_map: dict[str, str] = {n: n for n in old.tables if n in new.tables}
    for d in dropped_t:
        best = None
        for a in added_t:
            names_d = {c.name for c in old.tables[d].columns}
            names_a = {c.name for c in new.tables[a].columns}
            jacc = len(names_d & names_a) / max(1, len(names_d | names_a))
            score = round(max(jacc, _similar(d, a) * 0.6), 3)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, a)
        if best is not None:
            cand = RenameCandidate(kind="table", table=d, old=d, new=best[1], score=best[0])
            if confirmed.get(d) == best[1]:
                cand = cand.model_copy(update={"confirmed": True})
                table_map[d] = best[1]
            candidates.append(cand)
    renamed_old = {o for o, n in table_map.items() if o != n}
    renamed_new = {table_map[o] for o in renamed_old}
    for o in sorted(renamed_old):
        ops.append(
            ChangeOp(
                kind="RENAME_TABLE",
                table=o,
                detail={"new": table_map[o]},
                description=f"rename table {o} to {table_map[o]}",
            )
        )
    for d in dropped_t:
        if d not in renamed_old:
            ops.append(ChangeOp(kind="DROP_TABLE", table=d, description=f"drop table {d}"))
    for a in added_t:
        if a not in renamed_new:
            ops.append(ChangeOp(kind="ADD_TABLE", table=a, description=f"add table {a}"))

    # ---- per table
    for o_name, n_name in sorted(table_map.items()):
        ot, nt = old.tables[o_name], new.tables[n_name]
        ops.extend(_diff_table(ot, nt, confirmed, candidates))

    ordered = sorted(ops, key=lambda op: (op.table, op.kind, op.column or "", op.description))
    return SchemaDiff(
        ops=tuple(ordered), rename_candidates=tuple(candidates), warnings=tuple(warnings)
    )


def _diff_table(
    ot: Table, nt: Table, confirmed: dict[str, str], candidates: list[RenameCandidate]
) -> list[ChangeOp]:
    ops: list[ChangeOp] = []
    t = ot.name
    old_cols = {c.name: c for c in ot.columns}
    new_cols = {c.name: c for c in nt.columns}
    dropped = [c for n, c in old_cols.items() if n not in new_cols]
    added = [c for n, c in new_cols.items() if n not in old_cols]
    col_map = {n: n for n in old_cols if n in new_cols}
    # an explicit rename (ALTER ... RENAME COLUMN, or confirmed by the caller) is trusted as stated
    stated = [
        (c.name, confirmed[f"{t}.{c.name}"])
        for c in dropped
        if confirmed.get(f"{t}.{c.name}") in {a.name for a in added}
    ]
    for old_name, new_name in stated:
        dropped = [c for c in dropped if c.name != old_name]
        added = [c for c in added if c.name != new_name]
        col_map[old_name] = new_name
        candidates.append(
            RenameCandidate(
                kind="column", table=t, old=old_name, new=new_name, score=1.0, confirmed=True
            )
        )
        ops.append(
            ChangeOp(
                kind="RENAME_COLUMN",
                table=t,
                column=old_name,
                detail={"new": new_name},
                description=f"rename column {t}.{old_name} to {new_name}",
            )
        )
    for cand in _rename_pairs(dropped, added, t):
        if confirmed.get(f"{t}.{cand.old}") == cand.new:
            cand = cand.model_copy(update={"confirmed": True})
            col_map[cand.old] = cand.new
            ops.append(
                ChangeOp(
                    kind="RENAME_COLUMN",
                    table=t,
                    column=cand.old,
                    detail={"new": cand.new},
                    description=f"rename column {t}.{cand.old} to {cand.new}",
                )
            )
        candidates.append(cand)
    renamed_old = {o for o, n in col_map.items() if o != n}
    renamed_new = {col_map[o] for o in renamed_old}
    for c in dropped:
        if c.name not in renamed_old:
            ops.append(
                ChangeOp(
                    kind="DROP_COLUMN",
                    table=t,
                    column=c.name,
                    detail={"type": _type_text(c)},
                    description=f"drop column {t}.{c.name}",
                )
            )
    for c in added:
        if c.name not in renamed_new:
            ops.append(
                ChangeOp(
                    kind="ADD_COLUMN",
                    table=t,
                    column=c.name,
                    detail={
                        "type": _type_text(c),
                        "nullable": c.nullable,
                        "default": c.default.sql if c.default else None,
                    },
                    description=f"add column {t}.{c.name} {_type_text(c)}"
                    + ("" if c.nullable else " NOT NULL"),
                )
            )
    for o_col, n_col in sorted(col_map.items()):
        a, b = old_cols[o_col], new_cols[n_col]
        if _type_sig(a) != _type_sig(b):
            ops.append(
                ChangeOp(
                    kind="RETYPE_COLUMN",
                    table=t,
                    column=n_col,
                    detail={"from": _type_text(a), "to": _type_text(b)},
                    description=(
                        f"change type of {t}.{n_col} from {_type_text(a)} to {_type_text(b)}"
                    ),
                )
            )
        if a.nullable and not b.nullable:
            ops.append(
                ChangeOp(
                    kind="SET_NOT_NULL",
                    table=t,
                    column=n_col,
                    description=f"set NOT NULL on {t}.{n_col}",
                )
            )
        if not a.nullable and b.nullable:
            ops.append(
                ChangeOp(
                    kind="DROP_NOT_NULL",
                    table=t,
                    column=n_col,
                    description=f"drop NOT NULL on {t}.{n_col}",
                )
            )
        ad, bd = (a.default.sql if a.default else None), (b.default.sql if b.default else None)
        if ad != bd:
            ops.append(
                ChangeOp(
                    kind="SET_DEFAULT" if bd is not None else "DROP_DEFAULT",
                    table=t,
                    column=n_col,
                    detail={"from": ad, "to": bd},
                    description=f"{'set' if bd is not None else 'drop'} default on {t}.{n_col}"
                    + (f" to {bd}" if bd is not None else ""),
                )
            )
    # ---- keys and constraints
    mapped_pk = tuple(col_map.get(c, c) for c in (ot.primary_key.columns if ot.primary_key else ()))
    new_pk = nt.primary_key.columns if nt.primary_key else ()
    if mapped_pk != new_pk:
        ops.append(
            ChangeOp(
                kind="CHANGE_PRIMARY_KEY",
                table=t,
                detail={"from": list(mapped_pk), "to": list(new_pk)},
                description=(
                    f"change primary key of {t} from ({', '.join(mapped_pk)}) "
                    f"to ({', '.join(new_pk)})"
                ),
            )
        )

    def mapped(cols: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(col_map.get(c, c) for c in cols)

    old_fks = {
        _fk_key(f.model_copy(update={"columns": mapped(f.columns)})): f for f in ot.foreign_keys
    }
    new_fks = {_fk_key(f): f for f in nt.foreign_keys}
    for k, f in sorted(old_fks.items(), key=lambda kv: str(kv[0])):
        if k not in new_fks:
            ops.append(
                ChangeOp(
                    kind="DROP_FOREIGN_KEY",
                    table=t,
                    detail={"fk": _fk_text(f)},
                    description=f"drop foreign key {t} {_fk_text(f)}",
                )
            )
    for k, f in sorted(new_fks.items(), key=lambda kv: str(kv[0])):
        if k not in old_fks:
            ops.append(
                ChangeOp(
                    kind="ADD_FOREIGN_KEY",
                    table=t,
                    detail={"fk": _fk_text(f)},
                    description=f"add foreign key {t} {_fk_text(f)}",
                )
            )
    old_u = {mapped(u.columns): u for u in ot.uniques}
    new_u = {_unique_key(u): u for u in nt.uniques}
    for k in sorted(set(old_u) - set(new_u)):
        ops.append(
            ChangeOp(
                kind="DROP_UNIQUE",
                table=t,
                detail={"columns": list(k)},
                description=f"drop UNIQUE ({', '.join(k)}) on {t}",
            )
        )
    for k in sorted(set(new_u) - set(old_u)):
        ops.append(
            ChangeOp(
                kind="ADD_UNIQUE",
                table=t,
                detail={"columns": list(k)},
                description=f"add UNIQUE ({', '.join(k)}) on {t}",
            )
        )
    old_c = {_check_text(c): c for c in ot.checks}
    new_c = {_check_text(c): c for c in nt.checks}
    for ck in sorted(set(old_c) - set(new_c)):
        ops.append(
            ChangeOp(
                kind="DROP_CHECK",
                table=t,
                detail={"expression": ck},
                description=f"drop CHECK ({ck}) on {t}",
            )
        )
    for ck in sorted(set(new_c) - set(old_c)):
        ops.append(
            ChangeOp(
                kind="ADD_CHECK",
                table=t,
                detail={"expression": ck},
                description=f"add CHECK ({ck}) on {t}",
            )
        )
    old_i = {_index_key(i.model_copy(update={"columns": mapped(i.columns)})): i for i in ot.indexes}
    new_i = {_index_key(i): i for i in nt.indexes}
    for k in sorted(set(old_i) - set(new_i), key=str):
        i = old_i[k]
        ops.append(
            ChangeOp(
                kind="DROP_INDEX",
                table=t,
                detail={"name": i.name, "columns": list(k[0])},
                description=f"drop index {i.name} on {t}",
            )
        )
    for k in sorted(set(new_i) - set(old_i), key=str):
        i = new_i[k]
        ops.append(
            ChangeOp(
                kind="ADD_INDEX",
                table=t,
                detail={"name": i.name, "columns": list(k[0]), "unique": i.unique},
                description=f"add {'unique ' if i.unique else ''}index {i.name} on {t}",
            )
        )
    return ops


def _check_text(c: CheckConstraint) -> str:
    return " ".join(c.expression_sql.split())
