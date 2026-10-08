"""Prompt construction and strict-JSON parsing. Only schema *structure* is ever sent."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from schemashift.ai.base import AdvisorError
from schemashift.models.placement import AISuggestion
from schemashift.models.schema import Schema, Table
from schemashift.optimizer.features import RelationshipFeatures

PROMPT_VERSION = "placement-v1"

SYSTEM = (
    "You are a MongoDB data-modelling expert reviewing a PostgreSQL to MongoDB migration. "
    "For one foreign-key relationship, decide whether the child rows should be EMBEDDED in the "
    "parent document or stored in their own collection and REFERENCED. "
    "Reply with a single JSON object and nothing else."
)

RESPONSE_FORMAT = (
    '{"decision": "EMBED" | "REFERENCE", "confidence": <number between 0 and 1>, '
    '"justification": "<one or two sentences>"}'
)

_FEATURE_LABELS = (
    ("cardinality", "relationship cardinality"),
    ("read_together_ratio", "share of child reads that also read the parent (0-1)"),
    ("child_independent_access", "share of child reads that do not touch the parent (0-1)"),
    ("child_write_frequency", "child write frequency (low/med/high)"),
    ("estimated_child_count_per_parent", "estimated children per parent"),
    ("estimated_child_doc_size_bytes", "estimated child document size (bytes)"),
    ("parent_doc_size_bytes", "estimated parent document size (bytes)"),
    ("child_has_other_parents", "child also references other parent tables"),
    (
        "guarantees_needing_embedding",
        "SQL guarantees (cascade delete, multi-table transactions) that need embedding",
    ),
)


def render_table(table: Table) -> str:
    """Compact CREATE TABLE text (structure only, no data)."""
    from schemashift.verification.ddl import q, type_sql  # lazy: avoids an import cycle

    lines = []
    for c in table.columns:
        flags = "" if c.nullable else " NOT NULL"
        lines.append(f"  {q(c.name)} {type_sql(c.sql_type)}{flags}")
    if table.primary_key:
        lines.append(f"  PRIMARY KEY ({', '.join(q(c) for c in table.primary_key.columns)})")
    for fk in table.foreign_keys:
        lines.append(
            f"  FOREIGN KEY ({', '.join(q(c) for c in fk.columns)}) REFERENCES {q(fk.ref_table)} "
            f"({', '.join(q(c) for c in fk.ref_columns)}) ON DELETE {fk.on_delete}"
        )
    return f"CREATE TABLE {q(table.name)} (\n" + ",\n".join(lines) + "\n);"


def schema_excerpt(schema: Schema, rel: RelationshipFeatures) -> str:
    tables = [schema.tables[rel.parent]]
    if rel.child != rel.parent:
        tables.append(schema.tables[rel.child])
    return "\n\n".join(render_table(t) for t in tables)


def build_prompt(rel: RelationshipFeatures, excerpt: str) -> str:
    data = rel.model_dump()
    facts = "\n".join(f"- {label}: {data[key]}" for key, label in _FEATURE_LABELS)
    return (
        f"Relationship: {rel.child} (child) -> {rel.parent} (parent)\n\n"
        f"Table definitions:\n{excerpt}\n\n"
        f"Workload and size features:\n{facts}\n\n"
        "Considerations: MongoDB documents are limited to 16 MB; embedded arrays should be "
        "bounded; "
        "a child with several parents can be embedded in at most one; embedding makes parent+child "
        "reads and deletes atomic but removes independent access to the child.\n\n"
        f"Respond with strict JSON of this shape: {RESPONSE_FORMAT}"
    )


def parse_suggestion(text: str) -> AISuggestion:
    """Validate the model's reply; tolerates surrounding prose or a ```json fence."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise AdvisorError("no JSON object in the reply")
    try:
        data = json.loads(match.group(0))
        data["decision"] = str(data.get("decision", "")).strip().upper()
        return AISuggestion.model_validate(data)
    except (json.JSONDecodeError, ValidationError, AttributeError) as exc:
        raise AdvisorError(f"invalid suggestion: {exc}") from exc
