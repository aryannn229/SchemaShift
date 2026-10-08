"""Rule for multi-table transactions."""

from __future__ import annotations

from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.registry import Outcome, Trace, rule
from schemashift.ir.nodes import MultiEntityAtomicity


@rule(
    node_type=MultiEntityAtomicity,
    rule_id="EQ-ATOMIC",
    title="Multi-table transactions",
    outcomes=(
        Outcome(
            "SINGLE_DOCUMENT",
            "all touched tables embed into one document",
            "SAFE",
            "Transaction {node.txn_id} touches {tables}, which all live in one document of "
            "{root}; single-document writes are atomic.",
            mongo_feature="single-document atomicity",
        ),
        Outcome(
            "TRANSACTION",
            "tables are in several collections and multi-document transactions are available",
            "CHANGED",
            "Transaction {node.txn_id} spans {roots} collections ({tables}). Multi-document "
            "transactions work, but cost performance, have a 60s default limit and need a "
            "replica set.",
            "Wrap the writes in a session.with_transaction(...) block (generated helper).",
            "multi-document transactions",
        ),
        Outcome(
            "NO_TRANSACTION",
            "tables are in several collections and transactions are unavailable",
            "BROKEN",
            "Transaction {node.txn_id} spans {roots} collections ({tables}) and the target "
            "has no multi-document transactions, so partial failure leaves inconsistent data.",
            "Run MongoDB as a replica set / Atlas, or embed the tables into one document.",
        ),
    ),
)
def check_atomicity(
    node: MultiEntityAtomicity, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    roots = list(dict.fromkeys(ctx.root_collection(table) for table in node.tables))
    v = {"tables": ", ".join(node.tables), "roots": str(len(roots)), "root": roots[0]}
    if t.check("all tables share one root document", len(roots) == 1, ", ".join(roots)):
        return "SINGLE_DOCUMENT", v
    if t.check("transactions are available", ctx.options.transactions_available):
        return "TRANSACTION", v
    return "NO_TRANSACTION", v
