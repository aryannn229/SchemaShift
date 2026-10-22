"""Label provenance and the blind-labeling template.

A label file (``expected.yaml``) records who wrote the labels and whether they saw SchemaShift's
verdicts. Metrics are reported per provenance group and never merged:

* ``self_labeled``: written by the rule author, or by anyone who saw the verdicts;
* ``independent``: written by someone other than the rule author, blind (without looking at the
  verdicts). Only this group can support a claim of independent validation.
"""

from __future__ import annotations

from typing import Any

from schemashift.ir import IRProgram
from schemashift.models.base import FrozenModel

# labeled_by values that mean "the rule author" (case-insensitive)
RULE_AUTHORS = frozenset({"", "rule-author", "author", "claude", "claude-code", "assistant"})
GROUPS = ("self_labeled", "independent")


class Provenance(FrozenModel):
    labeled_by: str
    labeled_blind: bool

    @property
    def independent(self) -> bool:
        return self.labeled_blind and self.labeled_by.strip().lower() not in RULE_AUTHORS

    @property
    def group(self) -> str:
        return "independent" if self.independent else "self_labeled"


def provenance_of(expected: dict[str, Any]) -> Provenance:
    """Missing fields mean: written by the rule author, not blind."""
    return Provenance(
        labeled_by=str(expected.get("labeled_by") or "rule-author"),
        labeled_blind=bool(expected.get("labeled_blind", False)),
    )


HEADER = """\
# SchemaShift label file ({schema}).
#
# Label each guarantee below with the status you expect when this PostgreSQL schema is migrated to
# MongoDB 7 (replica set, so transactions work), WITHOUT running SchemaShift:
#   SAFE     the guarantee holds as before (MongoDB enforces it, or it cannot be violated)
#   CHANGED  it holds only with a different mechanism or weaker semantics (state it in `why`)
#   BROKEN   MongoDB cannot preserve it for the layout below
# `source` shows the SQL that produced the guarantee. Fill `placements` first: EMBED or REFERENCE
# per relationship decides which verdicts apply (REFERENCE is the default). Then set labeled_by to
# your name; keep labeled_blind: true only if you did not look at SchemaShift's output.
"""

MECHANICAL = ("type:", "not_null:")


def _excerpt(sql: str, node: Any) -> str:
    span = getattr(node, "source_span", None)
    if span is None:
        return ""
    lines = sql.splitlines()
    first = max(1, span.line_start)
    last = min(len(lines), max(first, span.line_end))
    text = " ".join(" ".join(lines[first - 1 : last]).split())
    return text[:160]


def label_template(
    name: str, sql: str, program: IRProgram, relationship_ids: list[str], include_mechanical: bool
) -> str:
    """An empty label file listing every guarantee (and nothing about SchemaShift's verdicts)."""
    out = [HEADER.format(schema=name)]
    out.append('labeled_by: ""        # your name (someone other than the rule author)')
    out.append("labeled_blind: true   # true = you did not look at SchemaShift's verdicts")
    out.append('description: ""')
    out.append("diagnostics: []       # expected diagnostic codes (SEM001, ...), if any")
    out.append("options: {}           # e.g. {transactions_available: false}")
    out.append("placements:")
    for rid in relationship_ids:
        out.append(f"  {rid}: REFERENCE   # EMBED | REFERENCE")
    if not relationship_ids:
        out.append("  {}")
    if not include_mechanical:
        out.append("bulk:")
        for pattern, why in (
            ("type:*", "column types"),
            ("not_null:*", "NOT NULL constraints"),
        ):
            out.append(f'  - {{match: "{pattern}", status: "", why: ""}}   # {why}')
    out.append("verdicts:")
    for node in program.nodes:
        node_id = node.id
        if node_id.startswith(("entity:", "attr:", "rel:")):
            continue
        if not include_mechanical and node_id.startswith(MECHANICAL):
            continue
        out.append(f"  {node_id}:")
        out.append('    status: ""   # SAFE | CHANGED | BROKEN')
        out.append('    why: ""')
        src = _excerpt(sql, node).replace('"', "'")
        if src:
            out.append(f'    source: "{src}"')
    return "\n".join(out) + "\n"
