# Evaluation methodology

This document is expanded in Phase 27 (`schemashift evaluate --all`). It currently covers the evaluation of the equivalence
checker and the optimizer (Phase 15). Numbers live in `docs/METRICS.md`; this file explains where they come from and what they
do not show.

## Evidence kinds, kept apart
1. **Self-labeled corpus** (`backend/tests/corpus/`, 32 schemas). The labels were written by the author of the rules from MongoDB
   semantics. Agreement shows that the implementation is consistent with the documented rule table; it is not independent
   validation.
2. **Independent corpus** (`backend/tests/corpus_independent/`). Labels written blind by someone other than the rule author
   (`labeled_by` not the author, `labeled_blind: true`). Empty until a second person labels; produced with
   `schemashift corpus label-template`. Scored separately and never merged with (1).
3. **Result-set correctness** against a real PostgreSQL, on the bundled samples and on public schemas
   (`samples/external/`). PostgreSQL is the oracle, so no labels are needed. This is the strongest label-free evidence; its
   limits are the hand-written workloads and synthetic data.
4. **Optimizer vs AI advisor agreement** (raw rate and Cohen's kappa). The mock advisor is a deterministic rule of thumb and is
   reported as such; the real-model figure needs an API key.
5. **Mutation score** (mutmut on `equivalence/` and `optimizer/`): how many deliberate code changes the tests notice.

## Metrics
Problem detection (CHANGED or BROKEN vs SAFE) precision/recall/F1, per-class one-vs-rest precision/recall/F1 and the 3x3
confusion matrix, per label group. Severity accuracy. Kappa. Mutation score = killed / all mutants (mutants no test covers count
as survivors).

## Threats to validity (more in Phase 27)
Author-written rules and labels; a small, mostly synthetic corpus; hand-written workloads; synthetic seed data; PostgreSQL
and MongoDB versions fixed to 16 and 7; the ICU-versus-libc string collation difference for punctuation.
