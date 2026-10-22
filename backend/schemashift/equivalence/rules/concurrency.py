"""Rules for concurrency scenarios: does the guarantee survive concurrent writers?

Verdict meaning here: **SAFE** (no interleaving breaks it, by construction), **CHANGED** (it holds
under stated conditions: transactions, the generated helpers), **BROKEN** (an interleaving breaks it).
The helpers' own behaviour is read from the shipped code (``concurrency.helpers``), not assumed.
"""

from __future__ import annotations

from schemashift.concurrency.scenarios import (
    ArrayWriteScenario,
    CounterScenario,
    DuplicateInsertScenario,
    FkRaceScenario,
    TransactionScenario,
)
from schemashift.equivalence.context import RuleContext
from schemashift.equivalence.registry import Outcome, Trace, rule


# ------------------------------------------------------------------------------- FK race
@rule(
    node_type=FkRaceScenario,
    rule_id="EQ-CONC-FK",
    title="Concurrent child insert vs parent delete",
    outcomes=(
        Outcome(
            "EMBEDDED",
            "child embedded in its parent",
            "SAFE",
            "{node.child} lives inside {node.parent} documents, so inserting a child and deleting "
            "its parent touch one document and MongoDB serializes the two writes.",
            mongo_feature="single-document atomicity",
        ),
        Outcome(
            "LOCKED",
            "child referenced, transactions available and the helpers lock the parent key",
            "CHANGED",
            "Inserting a {node.child} and deleting its {node.parent} write different documents, so "
            "snapshot isolation alone lets both commit and leave an orphan (write skew). The generated "
            "helpers make the two conflict through a lock document inside multi-document transactions; "
            "the loser aborts and is retried.",
            "Call insert_row / delete_row inside run_in_transaction, never outside it; expect retries "
            "on hot parents.",
            "multi-document transactions + lock documents",
        ),
        Outcome(
            "UNLOCKED",
            "child referenced, transactions available but the helpers do not lock",
            "BROKEN",
            "The helpers check that the {node.parent} exists and then insert the {node.child} as "
            "separate steps: a parent deleted in between leaves an orphan even inside a transaction "
            "(write skew).",
            "Take a lock document on the parent key in both the insert and the delete.",
        ),
        Outcome(
            "NO_TRANSACTION",
            "child referenced and transactions are unavailable",
            "BROKEN",
            "Without multi-document transactions the {node.parent} check and the {node.child} insert "
            "are separate operations: a parent deleted in between leaves an orphan child.",
            "Run MongoDB as a replica set (or Atlas) and use the transactional helpers.",
        ),
    ),
)
def check_fk_race(node: FkRaceScenario, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    if t.check(f"{node.child} is embedded in {node.parent}", ctx.is_embedded(node.fk_ref)):
        return "EMBEDDED", {}
    if not t.check("transactions are available", ctx.options.transactions_available):
        return "NO_TRANSACTION", {}
    locks = ctx.helpers.insert_locks_parent and ctx.helpers.delete_locks_row
    t.check(
        "insert_row and delete_row lock the parent key",
        locks,
        "read from the shipped helper source",
    )
    return ("LOCKED" if locks else "UNLOCKED"), {}


# ------------------------------------------------------------------- duplicate insert
@rule(
    node_type=DuplicateInsertScenario,
    rule_id="EQ-CONC-DUP",
    title="Concurrent inserts of the same key",
    outcomes=(
        Outcome(
            "INDEX",
            "table has its own collection",
            "SAFE",
            "Two concurrent inserts of the same {node.table} key: MongoDB's unique index admits exactly "
            "one; the other fails with DuplicateKeyError.",
            mongo_feature="unique index",
        ),
        Outcome(
            "ARRAY_ATOMIC",
            "table is an embedded array and the helper pushes with an atomic conditional update",
            "SAFE",
            "No index can enforce uniqueness inside one document's array, so the helper folds the check "
            "into the push: one conditional single-document update ($not/$elemMatch). Of two concurrent "
            "inserts of the same key only one matches.",
            mongo_feature="atomic conditional update",
        ),
        Outcome(
            "ARRAY_TXN",
            "embedded array without an atomic guard, transactions available",
            "CHANGED",
            "The duplicate check and the push are separate steps; inside a transaction the second writer "
            "conflicts on the host document and is retried, so the duplicate is rejected.",
            "Call the helper inside run_in_transaction.",
            "multi-document transactions",
        ),
        Outcome(
            "ARRAY_RACE",
            "embedded array without an atomic guard and no transactions",
            "BROKEN",
            "Two concurrent inserts of the same {node.table} key can both pass the duplicate check and "
            "both be pushed.",
            "Use transactions, or fold the check into the update.",
        ),
    ),
)
def check_duplicate_insert(
    node: DuplicateInsertScenario, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    if not t.check(
        f"{node.table} is embedded in another document", ctx.is_table_embedded(node.table)
    ):
        return "INDEX", {}
    atomic = ctx.helpers.array_insert_is_atomic
    t.check(
        "the helper's array insert is one conditional update", atomic, "read from helper source"
    )
    if atomic:
        return "ARRAY_ATOMIC", {}
    if t.check("transactions are available", ctx.options.transactions_available):
        return "ARRAY_TXN", {}
    return "ARRAY_RACE", {}


# ---------------------------------------------------------------------- array writes
@rule(
    node_type=ArrayWriteScenario,
    rule_id="EQ-CONC-ARRAY",
    title="Concurrent writes to one embedded array",
    outcomes=(
        Outcome(
            "ATOMIC",
            "always",
            "SAFE",
            "{node.table} rows are elements of {node.host} documents. Positional updates ($[x] with "
            "arrayFilters), $push and $pull are atomic per document, so concurrent writers to different "
            "elements do not lose each other's updates (two writers of the same field: last one wins, as "
            "under READ COMMITTED).",
            mongo_feature="single-document atomic updates",
        ),
    ),
)
def check_array_write(
    node: ArrayWriteScenario, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    t.check("writes use $set/$push/$pull with positional filters, never read-modify-write", True)
    return "ATOMIC", {}


# ------------------------------------------------------------------------ transactions
@rule(
    node_type=TransactionScenario,
    rule_id="EQ-CONC-TXN",
    title="Multi-collection transaction under write conflicts",
    outcomes=(
        Outcome(
            "SINGLE_DOCUMENT",
            "every touched table lives in one document",
            "SAFE",
            "The statements write one document, which MongoDB applies atomically and isolates.",
            mongo_feature="single-document atomicity",
        ),
        Outcome(
            "RETRIED",
            "several collections and transactions available",
            "CHANGED",
            "A write conflict aborts the transaction with TransientTransactionError and a network error "
            "can leave the commit result unknown (UnknownTransactionCommitResult); "
            "run_in_transaction retries both through with_transaction (up to 120 s).",
            "Keep the callback free of side effects other than database writes (it can run more than once).",
            "with_transaction retry loop",
        ),
        Outcome(
            "UNPROTECTED",
            "several collections and transactions unavailable",
            "BROKEN",
            "Without transactions the writes are independent: a concurrent reader can see some of them and "
            "a failure leaves the others applied.",
            "Run MongoDB as a replica set (or Atlas).",
        ),
    ),
)
def check_transaction(
    node: TransactionScenario, ctx: RuleContext, t: Trace
) -> tuple[str, dict[str, str]]:
    roots = {ctx.root_collection(table) for table in node.tables}
    if t.check("all touched tables embed into one document", len(roots) == 1):
        return "SINGLE_DOCUMENT", {}
    if not t.check("transactions are available", ctx.options.transactions_available):
        return "UNPROTECTED", {}
    t.check("run_in_transaction uses with_transaction", ctx.helpers.transactions_retry)
    return "RETRIED", {}


# ---------------------------------------------------------------------------- counters
@rule(
    node_type=CounterScenario,
    rule_id="EQ-CONC-COUNTER",
    title="Concurrent id generation",
    outcomes=(
        Outcome(
            "OBJECTID",
            "preserve_integer_ids is off",
            "SAFE",
            "{node.table}.{node.column} becomes an ObjectId generated by the driver: concurrent inserts "
            "never collide (the ids are unique but not sequential).",
            mongo_feature="ObjectId",
        ),
        Outcome(
            "COUNTER",
            "preserve_integer_ids is on and the counter is one atomic update",
            "CHANGED",
            "Concurrent inserts get distinct integers from one atomic findOneAndUpdate/$inc on a counter "
            "document. That document is a hot spot, and an id taken outside the transaction's session is "
            "not given back when the transaction aborts (a gap).",
            "Call next_id with the transaction's session so an aborted insert returns its number.",
            "atomic $inc counter",
        ),
        Outcome(
            "COUNTER_RACE",
            "preserve_integer_ids is on and the counter is not one atomic update",
            "BROKEN",
            "Reading the counter and writing it back are separate steps: two inserts can get the same id.",
            "Use a single findOneAndUpdate with $inc.",
        ),
    ),
)
def check_counter(node: CounterScenario, ctx: RuleContext, t: Trace) -> tuple[str, dict[str, str]]:
    if not t.check("preserve_integer_ids is on", ctx.options.preserve_integer_ids):
        return "OBJECTID", {}
    atomic = ctx.helpers.counter_is_atomic
    t.check("next_id is one atomic update", atomic, "read from helper source")
    return ("COUNTER" if atomic else "COUNTER_RACE"), {}
