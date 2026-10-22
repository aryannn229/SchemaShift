# SMT-backed constraint reasoning (Phase 26)

See SPEC.md section 27 for the fragment. This page records what the soundness test found, because that is the useful part.

Each of these was found by the random soundness test (a counterexample or proof that PostgreSQL 16 / MongoDB 7 contradicted), then fixed and pinned by a unit test:

| Found | Where | Fix |
|---|---|---|
| `x IS NOT NULL` was encoded as `IS NULL` (sqlglot writes it `Is(..., negate=True)`) | the encoder | read `negate` |
| two `IN` checks with an empty intersection produced `enum: []`, which MongoDB refuses (`createCollection` fails) | **code generator** | contradictory lists become `not: {}` (no value passes) |
| `CHECK (c * 2 >= 0)` was "proven": PostgreSQL raises on 32-bit overflow, MongoDB does not | the encoder | integer arithmetic carries its width and an overflow rejects the row |
| `NUMERIC(10,0)`, `CHECK (c <= 0)`: PostgreSQL accepts 0.005 (it rounds to 0 before the CHECK) | the encoder | the stored value is the rounded value |
| `CHECK (LENGTH(s) >= 2 OR s IS NULL)`: MongoDB rejected rows with `s` missing (the guard `$lte: [{$strLenCP: "$s"}, null]` raises on a missing `s`) | **code generator** | guards test the argument of `$strLenCP` |

Two of the five are genuine bugs in the generated MongoDB validators that no earlier test had found; three were mistakes in the first version of the encoder.

## Not found by the soundness test: thread safety
Z3's default context is not thread safe. The API serves requests on a thread pool, and two simultaneous compiles crashed the server process (an access violation
inside `libz3`); Playwright found it. `run_smt` now holds a process-wide lock (`tests/unit/equivalence/test_smt.py::test_concurrent_compiles_do_not_crash_the_process`
fails with "Context mismatch" errors when the lock is removed).
