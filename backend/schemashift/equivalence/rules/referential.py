"""Rules for foreign-key guarantees: integrity, cascades, SET NULL/DEFAULT and RESTRICT."""

from __future__ import annotations

from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.registry import Outcome, Trace, rule
from schemashift.ir.nodes import (
    CascadingDelete,
    CascadingUpdate,
    ReferentialIntegrity,
    RestrictDelete,
    SetDefaultOnDelete,
    SetNullOnDelete,
)


def _embedded(ctx: RuleContext, fk_ref: str, t: Trace, child: str, parent: str) -> bool:
    return t.check(f"{child} is embedded in {parent}", ctx.is_embedded(fk_ref))


# ---------------------------------------------------------------- ReferentialIntegrity
@rule(
    node_type=ReferentialIntegrity,
    rule_id="EQ-REF-INTEGRITY",
    title="Foreign key referential integrity",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "child is embedded in its parent",
            "SAFE",
            "{node.child} rows live inside their {node.parent} document, so a child cannot "
            "reference a parent that does not exist.",
            mongo_feature="embedded sub-document",
        ),
        Outcome(
            "REFERENCED",
            "child is stored in its own collection",
            "CHANGED",
            "MongoDB has no foreign keys: {node.child}({cols}) may point at a {node.parent} "
            "that does not exist.",
            "Validate the parent exists in the application layer, inside a transaction when "
            "inserting or updating the child.",
        ),
    ),
)
def check_referential_integrity(
    node: ReferentialIntegrity, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    cols = {"cols": ", ".join(node.columns)}
    if _embedded(ctx, node.fk_ref, t, node.child, node.parent):
        return "EMBEDDED", cols
    return "REFERENCED", cols


# ------------------------------------------------------------------- CascadingDelete
@rule(
    node_type=CascadingDelete,
    rule_id="EQ-CASCADE-DEL",
    title="ON DELETE CASCADE",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "child embedded and every deeper cascade hop is embedded too",
            "SAFE",
            "Deleting a {node.parent} document deletes its embedded {node.child} data, "
            "matching ON DELETE CASCADE.",
            mongo_feature="embedded sub-document",
        ),
        Outcome(
            "REFERENCED",
            "child stored by reference and the cascade has no deeper hops",
            "BROKEN",
            "MongoDB has no native cascade: deleting a {node.parent} leaves its {node.child} "
            "documents orphaned.",
            "Delete the {node.child} documents in the same multi-document transaction as the "
            "{node.parent}, or run a change-stream worker that removes orphans.",
            "multi-document transactions",
        ),
        Outcome(
            "CHAIN",
            "cascade chain deeper than one hop and some hop is stored by reference",
            "BROKEN",
            "The cascade chain {chain} cannot be preserved: at least one hop is stored by "
            "reference, and MongoDB never cascades deletes.",
            "Delete every level of the chain ({chain}) in one multi-document transaction, or "
            "embed the whole chain, or use a change-stream worker.",
            "multi-document transactions",
        ),
    ),
)
def check_cascading_delete(
    node: CascadingDelete, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    rel = ctx.graph.relationship(node.fk_ref)
    hops = ctx.cascade_hops(rel)
    embedded = _embedded(ctx, node.fk_ref, t, node.child, node.parent)
    deeper = t.check("cascade continues below the child (depth > 1)", bool(hops))
    referenced_hop = t.check(
        "some deeper hop is stored by reference", any(not ctx.is_embedded(h.id) for h in hops)
    )
    chain = f"{node.parent} -> {node.child}"
    for hop in hops:
        mode = "embedded" if ctx.is_embedded(hop.id) else "by reference"
        chain += f" -> {hop.child} ({mode})"
    chain_vars = {"chain": chain}
    if embedded and not referenced_hop:
        return "EMBEDDED", chain_vars
    if deeper:
        return "CHAIN", chain_vars
    return "REFERENCED", chain_vars


# ------------------------------------------------------------------- CascadingUpdate
@rule(
    node_type=CascadingUpdate,
    rule_id="EQ-CASCADE-UPD",
    title="ON UPDATE CASCADE",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "child is embedded in its parent",
            "SAFE",
            "{node.child} is embedded in {node.parent}, so there are no copies of the key to "
            "keep in sync.",
            mongo_feature="embedded sub-document",
        ),
        Outcome(
            "IMMUTABLE_KEY",
            "child referenced and the parent key is the primary key (stored as immutable _id)",
            "CHANGED",
            "The key of {node.parent} is its primary key, stored as the immutable _id, so it "
            "does not change in practice, but nothing enforces propagation if it did.",
            "Treat the key as immutable; if it must change, copy the document and update every "
            "{node.child} reference in a transaction.",
        ),
        Outcome(
            "MUTABLE_KEY",
            "child referenced and the parent key is a mutable natural key",
            "BROKEN",
            "{node.parent}({cols}) is a mutable key and {node.child} stores copies of it; "
            "MongoDB will not propagate updates, leaving dangling references.",
            "Update {node.child} in the same transaction whenever {cols} changes, or reference "
            "the immutable _id instead of the natural key.",
            "multi-document transactions",
        ),
    ),
)
def check_cascading_update(
    node: CascadingUpdate, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    cols = {"cols": ", ".join(node.parent_columns)}
    if _embedded(ctx, node.fk_ref, t, node.child, node.parent):
        return "EMBEDDED", cols
    pk = ctx.graph.schema.tables[node.parent].primary_key
    is_pk = t.check(
        "parent key is the primary key",
        pk is not None and set(pk.columns) == set(node.parent_columns),
    )
    return ("IMMUTABLE_KEY" if is_pk else "MUTABLE_KEY"), cols


# --------------------------------------------------- SetNullOnDelete / SetDefaultOnDelete
def _set_outcomes(verb: str) -> tuple[Outcome, ...]:
    return (
        Outcome(
            "EMBEDDED",
            "child is embedded in its parent",
            "CHANGED",
            "{node.child} is embedded in {node.parent}: deleting the parent removes the child "
            f"entirely instead of {verb}.",
            "If the child must outlive the parent, store it by reference instead of embedding it.",
            "embedded sub-document",
        ),
        Outcome(
            "REFERENCED",
            "child stored by reference",
            "BROKEN",
            f"MongoDB will not {verb} {{node.child}}({{cols}}) when a {{node.parent}} is "
            "deleted; references are left dangling.",
            f"In the delete routine, update the {{node.child}} documents ({verb}) inside a "
            "transaction before deleting the parent.",
            "multi-document transactions",
        ),
    )


def _set_rule(
    node: SetNullOnDelete | SetDefaultOnDelete, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    cols = {"cols": ", ".join(node.columns)}
    if _embedded(ctx, node.fk_ref, t, node.child, node.parent):
        return "EMBEDDED", cols
    return "REFERENCED", cols


@rule(
    node_type=SetNullOnDelete,
    rule_id="EQ-SET-NULL",
    title="ON DELETE SET NULL",
    outcomes=_set_outcomes("set them to NULL"),
)
def check_set_null(node: SetNullOnDelete, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    return _set_rule(node, ctx, t)


@rule(
    node_type=SetDefaultOnDelete,
    rule_id="EQ-SET-DEFAULT",
    title="ON DELETE SET DEFAULT",
    outcomes=_set_outcomes("reset them to their default"),
)
def check_set_default(
    node: SetDefaultOnDelete, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    return _set_rule(node, ctx, t)


# --------------------------------------------------------------------- RestrictDelete
@rule(
    node_type=RestrictDelete,
    rule_id="EQ-RESTRICT",
    title="ON DELETE RESTRICT / NO ACTION",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "child is embedded in its parent",
            "CHANGED",
            "Deleting a {node.parent} always removes its embedded {node.child} data; the "
            "restriction ({node.action}) is lost.",
            "Check for embedded {node.child} entries in the application before deleting the "
            "parent, or store the child by reference.",
        ),
        Outcome(
            "REFERENCED",
            "child stored by reference",
            "CHANGED",
            "MongoDB will not block deleting a {node.parent} that still has {node.child} "
            "documents ({node.action}).",
            "Count {node.child} documents for the parent before deleting, inside a transaction.",
            "multi-document transactions",
        ),
    ),
)
def check_restrict(node: RestrictDelete, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    if _embedded(ctx, node.fk_ref, t, node.child, node.parent):
        return "EMBEDDED", {}
    return "REFERENCED", {}
