"""Data migration script (PostgreSQL -> MongoDB) generator."""

from __future__ import annotations

import pprint
from pathlib import Path
from typing import Any

from schemashift.codegen.emit import GeneratedFile, Header, py_header
from schemashift.codegen.layout import TableLayout
from schemashift.models.schema import Schema
from schemashift.pipeline import CompileOptions, CompileResult

RUNTIME = Path(__file__).with_name("runtime_migrate.py")


def table_plan(schema: Schema, layouts: dict[str, TableLayout]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for name, layout in layouts.items():
        t = schema.tables[name]
        tables[name] = {
            "kind": layout.kind,
            "collection": layout.collection,
            "host": layout.host,
            "path": list(layout.path),
            "dropped": list(layout.dropped_columns),
            "host_columns": list(layout.host_columns),
            "id_column": layout.id_column,
            "fields": dict(layout.fields),
            "scalar_column": layout.scalar_column,
            "pk": list(t.primary_key.columns) if t.primary_key else [],
            "identity": [c.name for c in t.columns if c.is_identity],
            "columns": {
                c.name: {
                    "base": c.sql_type.base,
                    "is_array": c.sql_type.is_array,
                    "nullable": c.nullable,
                }
                for c in t.columns
            },
        }
    return tables


def migration_plan(
    schema: Schema, layouts: dict[str, TableLayout], options: CompileOptions
) -> dict[str, Any]:
    """JSON-compatible description of the layout, consumed by the migration runtime."""
    return {
        "options": {
            "uuid_as": options.uuid_as,
            "preserve_integer_ids": options.preserve_integer_ids,
        },
        "roots": sorted(n for n, lay in layouts.items() if lay.is_root),
        "tables": table_plan(schema, layouts),
    }


def runtime_source() -> str:
    return RUNTIME.read_text(encoding="utf-8")


def generate_migration(
    result: CompileResult,
    layouts: dict[str, TableLayout],
    options: CompileOptions,
    header: Header,
) -> GeneratedFile:
    plan = migration_plan(result.schema_, layouts, options)
    source = runtime_source()
    marker = "# --- Runtime of the generated"
    body = source[source.index("from __future__") :]
    head, rest = body.split(marker, 1)
    content = (
        py_header(header)
        + "# Data migration PostgreSQL -> MongoDB: reads each table, builds the nested\n"
        + "# documents of the placement plan and upserts them in batches of 1000\n"
        + "# (idempotent, by _id).\n"
        + "# Usage: python migrate.py --source postgresql://... --target mongodb://...\n"
        + "#        --database mydb\n\n"
        + head
        + f"PLAN = {pprint.pformat(plan, width=100, sort_dicts=False)}\n\n\n"
        + marker
        + rest
    )
    return GeneratedFile(
        path="python/migrate.py", language="python", purpose="migration", content=content
    )
