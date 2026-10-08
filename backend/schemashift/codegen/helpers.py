"""Application-level enforcement helpers for CHANGED / BROKEN verdicts (Python, pymongo)."""

from __future__ import annotations

import pprint
import re
from pathlib import Path
from typing import Any

import sqlglot

from schemashift.codegen.collections import UntranslatedCheck
from schemashift.codegen.emit import GeneratedFile, Header, py_header
from schemashift.codegen.layout import TableLayout
from schemashift.codegen.migration import migration_plan
from schemashift.codegen.predicate import Untranslatable, literal_value
from schemashift.ir.nodes import (
    AutoIncrement,
    CascadingDelete,
    CascadingUpdate,
    CrossEntityUniqueness,
    DefaultValue,
    EntityUniqueness,
    MultiEntityAtomicity,
    ReferentialIntegrity,
    RestrictDelete,
    SetDefaultOnDelete,
    SetNullOnDelete,
    ValueUniqueness,
)
from schemashift.models.verdict import Verdict
from schemashift.pipeline import CompileOptions, CompileResult

RUNTIME = Path(__file__).with_name("runtime_helpers.py")
MARKER = "# --- Runtime of the generated"


def ident(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_").lower() or "t"


def helper_plan(
    result: CompileResult, layouts: dict[str, TableLayout], options: CompileOptions
) -> dict[str, Any]:
    plan = migration_plan(result.schema_, layouts, options)
    relationships = []
    for rel in result.graph.relationships.values():
        lay = layouts[rel.child]
        embedded = lay.host == rel.parent and set(rel.fk.columns) == set(lay.dropped_columns)
        relationships.append(
            {
                "id": rel.id,
                "child": rel.child,
                "parent": rel.parent,
                "columns": list(rel.fk.columns),
                "ref_columns": list(rel.fk.ref_columns),
                "on_delete": rel.fk.on_delete,
                "on_update": rel.fk.on_update,
                "embedded": embedded,
            }
        )
    defaults: dict[str, dict[str, Any]] = {}
    unique: dict[str, list[list[str]]] = {}
    for table in result.schema_.tables.values():
        for col in table.columns:
            if col.default is None or col.default.kind == "expression":
                continue
            if col.default.kind == "now":
                defaults.setdefault(table.name, {})[col.name] = {"kind": "now", "value": None}
                continue
            try:
                value = literal_value(sqlglot.parse_one(col.default.sql, dialect="postgres"), None)
            except Untranslatable:
                continue
            if value is not None and not isinstance(value, (str, int, float, bool)):
                continue
            defaults.setdefault(table.name, {})[col.name] = {"kind": "literal", "value": value}
        keys = [list(u.columns) for u in table.uniques]
        keys += [list(i.columns) for i in table.indexes if i.unique and i.where_sql is None]
        if keys:
            unique[table.name] = keys
    plan["relationships"] = relationships
    plan["defaults"] = defaults
    plan["unique"] = unique
    return plan


def _mitigations(result: CompileResult) -> dict[str, list[Verdict]]:
    """helper function name -> verdicts it works around."""
    out: dict[str, list[Verdict]] = {}
    for v in result.equivalence.final.verdicts:
        if v.status == "SAFE" or not v.mitigation:
            continue
        node = result.ir.by_id(v.node_id)
        names: list[str] = []
        if isinstance(node, ReferentialIntegrity):
            names = [f"insert_{ident(node.child)}"]
        elif isinstance(
            node, (CascadingDelete, SetNullOnDelete, SetDefaultOnDelete, RestrictDelete)
        ):
            names = [f"delete_{ident(node.parent)}"]
        elif isinstance(node, CascadingUpdate):
            names = [f"update_{ident(node.parent)}"]
        elif isinstance(node, (DefaultValue, AutoIncrement)):
            names = [f"insert_{ident(node.table)}"]
        elif isinstance(node, (EntityUniqueness, ValueUniqueness)):
            names = [f"insert_{ident(node.table)}"]
        elif isinstance(node, CrossEntityUniqueness):
            names = [f"insert_{ident(node.junction)}"]
        elif isinstance(node, MultiEntityAtomicity):
            names = [f"txn_{node.txn_id} (queries.py)"]
        for n in names:
            out.setdefault(n, []).append(v)
    return out


def generate_helpers(
    result: CompileResult,
    layouts: dict[str, TableLayout],
    options: CompileOptions,
    header: Header,
    untranslated: list[UntranslatedCheck],
) -> GeneratedFile:
    plan = helper_plan(result, layouts, options)
    source = RUNTIME.read_text(encoding="utf-8")
    body = source[source.index("from __future__") :]
    head, rest = body.split(MARKER, 1)
    mitigations = _mitigations(result)
    parent_tables = {r["parent"] for r in plan["relationships"]}
    wrappers: list[str] = []

    def mitigates(name: str) -> str:
        verdicts = mitigations.get(name, [])
        if not verdicts:
            return ""
        lines = ["    # Mitigates:"] + [
            f"    #   {v.node_id} ({v.status}): {v.rule_id}" for v in verdicts
        ]
        return "\n".join(lines) + "\n"

    for table in sorted(result.schema_.tables):
        n = ident(table)
        wrappers.append(
            f"def insert_{n}(db, values, session=None):\n"
            f"{mitigates(f'insert_{n}')}"
            f'    """INSERT INTO {table}: defaults, identity ids, FK and uniqueness checks."""\n'
            f"    return insert_row(db, PLAN, {table!r}, values, session)\n"
        )
        wrappers.append(
            f"def delete_{n}(client, db, **match):\n"
            f"{mitigates(f'delete_{n}')}"
            f'    """DELETE FROM {table} WHERE <match> honouring ON DELETE (one transaction)."""\n'
            f"    return run_in_transaction(\n"
            f"        client, lambda session: delete_row(db, PLAN, {table!r}, match, session)\n"
            f"    )\n"
        )
        if table in parent_tables:
            wrappers.append(
                f"def update_{n}(client, db, match, changes):\n"
                f"{mitigates(f'update_{n}')}"
                f'    """UPDATE {table} SET <changes> WHERE <match> honouring ON UPDATE."""\n'
                f"    return run_in_transaction(\n"
                f"        client,\n"
                f"        lambda session: update_row(\n"
                f"            db, PLAN, {table!r}, match, changes, session\n"
                f"        ),\n"
                f"    )\n"
            )
    for u in untranslated:
        label = ident(u.name or "check")
        wrappers.append(
            f"def check_{ident(u.table)}_{label}(doc):\n"
            f"    # Mitigates the untranslatable CHECK on {u.table}: {u.sql}\n"
            f'    """Application-side check for: {u.sql}  (untranslated: {u.fragment})"""\n'
            f'    raise NotImplementedError("implement this CHECK in application code")\n'
        )
    content = (
        py_header(header)
        + "# Application-level enforcement helpers. MongoDB has no foreign keys, cascades,\n"
        + "# defaults or sequences: these functions re-create the guarantees marked\n"
        + "# CHANGED or BROKEN by the equivalence checker.\n"
        + "# Requires migrate.py next to this file (or schemashift installed).\n\n"
        + head
        + f"PLAN = {pprint.pformat(plan, width=100, sort_dicts=False)}\n\n\n"
        + MARKER
        + rest
        + "\n\n# ---- per-table helpers (generated) ------------------------------------------\n\n"
        + "\n\n".join(wrappers)
    )
    mitigated_ids = tuple(sorted({v.node_id for vs in mitigations.values() for v in vs}))
    return GeneratedFile(
        path="python/helpers.py",
        language="python",
        purpose="helpers",
        content=content,
        mitigates=mitigated_ids,
    )
