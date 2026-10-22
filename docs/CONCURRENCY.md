# Transaction and concurrency guarantees (Phase 17)

A guarantee that MongoDB supports in theory is not automatically one the **generated implementation** keeps when two
writers meet. This page explains what SchemaShift analyses, what the generated helpers do about it, and how the claims are
tested.

## Scenarios and rules (`concurrency/`, `equivalence/rules/concurrency.py`)
Scenarios are derived from the guarantee IR and the placement plan (they are not IR nodes, so the labeled corpus is unaffected).
Each family has a rule in the registry; verdicts are SAFE (no interleaving breaks it by construction), CHANGED (holds under stated
conditions) or BROKEN (an interleaving breaks it), with evidence levels like every other verdict.

| Rule | Scenario | Outcomes |
|---|---|---|
| `EQ-CONC-FK` | a child insert races a delete of its referenced parent | EMBEDDED safe; LOCKED changed (lock documents inside transactions); UNLOCKED / NO_TRANSACTION broken (write skew leaves an orphan) |
| `EQ-CONC-DUP` | two inserts of the same key | INDEX safe (unique index); ARRAY_ATOMIC safe (one conditional update); ARRAY_TXN changed; ARRAY_RACE broken |
| `EQ-CONC-ARRAY` | concurrent writes to one embedded array | ATOMIC safe (positional updates are atomic per document) |
| `EQ-CONC-TXN` | a multi-collection transaction under write conflicts | SINGLE_DOCUMENT safe; RETRIED changed (`with_transaction` retries `TransientTransactionError` and `UnknownTransactionCommitResult`); UNPROTECTED broken |
| `EQ-CONC-COUNTER` | concurrent id generation | OBJECTID safe; COUNTER changed (atomic `$inc`, hot document, gaps outside the session); COUNTER_RACE broken |

## Static analysis of the helpers (`concurrency/helpers.py`)
The rules do not assume what the helpers do: `analyze_source` parses `codegen/runtime_helpers.py` with `ast` and reports, per
function, the reads, writes, lock calls and strings it contains. A function that reads and writes with no lock and no atomic
guard is a check-then-act sequence (it can race). The shipped helpers are profiled once; the LOCKED/UNLOCKED, ARRAY_ATOMIC and
COUNTER outcomes follow from that profile, so removing a safeguard from the helpers changes the verdicts (a unit test does exactly
that).

## What the generated code does (fixes made in this phase)
- `lock_row`: snapshot isolation only detects conflicts between writes to the *same* document, so a child insert that reads its
  parent cannot conflict with a delete of the parent (write skew). Child inserts, parent deletes and key updates all write one
  lock document (`_locks`) inside their transactions; the later one aborts with a write conflict and `with_transaction` retries
  it against the committed state. No effect outside a transaction.
- Inserts into an embedded array fold the duplicate check into the push (`$not` / `$elemMatch` in the update filter): one atomic
  conditional single-document update, which no index can provide inside an array.
- `insert_<table>_tx(client, db, values)` runs an insert in one retried transaction (use it under concurrent writers).
- `GUARDS = False` switches the guards off; only the probes use it, to show what the unguarded version lets through.

## Probes (`verification/concurrency.py`)
For each scenario the harness races writers against the sandbox replica set for a number of rounds (barrier start, seeded
sub-2 ms jitter, so a run is reproducible in what it does while the interleavings are the database's). Each probe runs twice:
`generated` (the helpers as shipped) and `unguarded` (the same operations without the guard). Reported per probe: writers,
rounds, violations (orphans, duplicates, repeated ids, lost updates), seed and duration. Scenarios that cannot be probed (a key
type that cannot be varied, a side nested inside another document) are listed with the reason.

Example (seed 3, 60 rounds, local run): the FK race produced 0 orphans with the generated helpers and 60 of 60 without the lock;
embedded-array duplicates 0 vs 60; the counter 0 duplicate ids vs 86 of 120; the transaction probe 0 lost updates with the
callback retried 127 times for 60 updates, vs 90 lost updates without a transaction.

**Zero violations is test evidence, not proof.** It supports the verdict but does not lift its conditions (a replica set, the
helpers used inside `run_in_transaction`, an idempotent callback).

## Limits
- Hot lock documents serialize writers that touch the same parent: correctness over throughput.
- The retry loop is the driver's `with_transaction` (120 s ceiling); a callback with side effects other than database writes can
  run more than once.
- Without transactions (standalone MongoDB) the FK and array guarantees are BROKEN under concurrency; the verdicts say so.
- Nested arrays more than one level deep have no atomic guard; the rule reports ARRAY_TXN or ARRAY_RACE.
