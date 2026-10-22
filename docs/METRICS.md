# Metrics

Computed by `schemashift corpus report` (see `docs/EVALUATION.md` for methodology).

- **Date:** 2026-10-23
- **Commit:** 01b25b0

The two label groups are scored **separately and never merged into one headline number**: the
self-labeled corpus is written by the author of the rules, so agreement shows internal consistency,
not independent validation. Only the independent group (labeled blind by someone else) can support
that claim, and it is currently empty.

| Evidence | Corpus | Result |
|---|---|---|
| Self-labeled (author) | 32 schemas, 883 guarantees | problem-detection recall 100.0%, precision 100.0%, F1 100.0%; severity accuracy 100.0% |
| Independent (blind, another person) | 0 schemas | **pending**: needs a second labeler, see "How to add independent labels" |
| Public schemas, result-set correctness vs real PostgreSQL | 2 verifiable of 5 | 31/31 queries MATCH (100.0%); needs no labels |
| Bundled samples, result-set correctness | 4 schemas | 21/21 MATCH |

## Self-labeled corpus (`backend/tests/corpus/`)

32 schemas, 883 labeled guarantees; labeled by rule-author.

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| problem detection (CHANGED or BROKEN) | 100.0% | 100.0% | 100.0% | 225 |
| SAFE | 100.0% | 100.0% | 100.0% | 658 |
| CHANGED | 100.0% | 100.0% | 100.0% | 196 |
| BROKEN | 100.0% | 100.0% | 100.0% | 29 |

Severity accuracy: 100.0%.

```
confusion (rows = label, cols = predicted)
               SAFE  CHANGED   BROKEN
SAFE            658        0        0
CHANGED           0      196        0
BROKEN            0        0       29
```

## Independent corpus (`backend/tests/corpus_independent/`)

No labeled schemas in this group yet.

### How to add independent labels
1. `schemashift corpus label-template samples/external/chinook/schema.sql --out backend/tests/corpus_independent`
   writes an empty `expected.yaml` listing every guarantee and the SQL that produced it (no verdicts).
2. Someone other than the rule author fills `status` for each node from MongoDB semantics, **without running
   SchemaShift**, sets `labeled_by` to their name and keeps `labeled_blind: true`.
3. `schemashift corpus report --write-docs` scores it in the independent group above.

## Public schemas (`samples/external/`)

Not written for SchemaShift; see each folder's README for source, license and porting notes. No labels are involved:
the diagnostics are what the parser/analyzer reports, the result sets are compared with a real PostgreSQL.

| Schema | Tables | Guarantees (SAFE/CHANGED/BROKEN) | Parse/analysis diagnostics | Queries translated | Result sets MATCH |
|---|---|---|---|---|---|
| chinook | 11 | 149 (121/27/1) | SEM011×1 | 16/16 | 16/16 |
| graphile_starter | 12 | 196 (126/68/2) | UNSUPPORTED_STATEMENT×57, UNSUPPORTED_FUNCTION×40, UNSUPPORTED_TRIGGER×15, UNSUPPORTED_ALTER_ACTION×11 | 3/3 | not verifiable (schema has unsupported constructs) |
| northwind | 14 | 181 (148/32/1) | SEM011×1 | 15/15 | 15/15 |
| pagila | 69 | 1150 (819/331/0) | UNSUPPORTED_ALTER_ACTION×55, SEM008×55, UNSUPPORTED_STATEMENT×16, UNSUPPORTED_TRIGGER×16 | 8/11 | not verifiable (schema has unsupported constructs) |
| supabase_slack_clone | 5 | 68 (49/17/2) | UNSUPPORTED_STATEMENT×18, UNSUPPORTED_ALTER_ACTION×8, UNSUPPORTED_FUNCTION×3, UNSUPPORTED_TRIGGER×1 | 8/8 | not verifiable (schema has unsupported constructs) |

Schemas with parse/analysis errors cannot be verified (the harness refuses to run them); their diagnostics are
the inventory of real-world constructs SchemaShift does not support yet.

## Optimizer vs AI advisor

| Advisor | Relationships | Raw agreement | Cohen's κ | Disagreements |
|---|---|---|---|---|
| mock (deterministic rule of thumb, **not** an AI signal) | 165 | 71.5% | 0.41 | 47 |
| Anthropic model | not measured | | | |

Disagreements (mock), optimizer vs advisor, both rationales:

| Case | Relationship | Optimizer | Advisor |
|---|---|---|---|
| corpus/03_fk_reference | `fk:orders.customer_id->customers.id` | **REFERENCE**: embed score 0.46 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/04_fk_embedded | `fk:orders.customer_id->customers.id` | **REFERENCE**: embed score 0.46 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/10_set_null_default | `fk:c1.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/10_set_null_default | `fk:c2.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/10_set_null_default | `fk:c3.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/11_restrict_actions | `fk:c1.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/11_restrict_actions | `fk:c2.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/11_restrict_actions | `fk:c3.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/12_update_cascade | `fk:c1.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/12_update_cascade | `fk:c2.p_code->p.code` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/12_update_cascade | `fk:c3.p_id->p.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/17_multi_parent_child | `fk:reviews.product_id->products.id` | **EMBED**: embed score 0.56 >= threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **REFERENCE** (0.60): mock advisor: default to a reference |
| corpus/19_unique_embedded_child | `fk:c.p_id->p.id` | **REFERENCE**: embed score 0.46 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/23_transactions_embedded | `fk:orders.customer_id->customers.id` | **REFERENCE**: embed score 0.46 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_write_frequency -0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/24_transactions_unavailable | `fk:orders.customer_id->customers.id` | **REFERENCE**: embed score 0.46 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_write_frequency -0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/25_joins | `fk:notes.order_id->orders.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.30; relationship_shape +0.12) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/27_circular_fk | `fk:a.b_id->b.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/27_circular_fk | `fk:b.a_id->a.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/28_semantic_errors | `fk:b.a_missing->a.nope` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/28_semantic_errors | `fk:b.a_txt->a.id` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/28_semantic_errors | `fk:b.a_x->a.x` | **REFERENCE**: hard constraint: the foreign key is nullable: a child row without a parent would have no document to live in and would be lost on embedding (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.06) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/30_ecommerce | `fk:order_items.order_id->orders.id` | **EMBED**: embed score 0.82 >= threshold 0.50 (read_together_ratio +0.30; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **REFERENCE** (0.60): mock advisor: default to a reference |
| corpus/30_ecommerce | `fk:orders.customer_id->customers.id` | **REFERENCE**: embed score 0.40 < threshold 0.50 (read_together_ratio +0.15; relationship_shape +0.12; child_independent_access -0.10) | **EMBED** (0.60): mock advisor: small single-parent child read together with its parent |
| corpus/30_ecommerce | `fk:reviews.product_id->products.id` | **EMBED**: embed score 0.82 >= threshold 0.50 (read_together_ratio +0.30; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **REFERENCE** (0.60): mock advisor: default to a reference |
| corpus/31_university | `fk:enrollments.student_id->students.id` | **EMBED**: embed score 0.75 >= threshold 0.50 (read_together_ratio +0.30; relationship_shape +0.12; guarantees_needing_embedding +0.10) | **REFERENCE** (0.60): mock advisor: default to a reference |

… and 22 more in docs/metrics.json.

## Mutation testing

Mutation score 89.1%: 1446 of 1622 mutants killed (163 survived, 13 timed out) in schemashift/equivalence/, schemashift/optimizer/. Command: `scripts/mutation.sh 8`. The first run scored 83.3%; tests were added for the survivors (exact reasons, scores, type map, predicate analysis, RULES.md). Mutants in code that runs at import time (the rule definitions) are not re-evaluated per mutant by mutmut's fork model, so most of the remaining survivors cannot be killed by unit tests; the exact RULES.md test covers that wording.

## Failure cases

False positives and false negatives of the self-labeled corpus:

None. Every labeled guarantee in the self-labeled corpus is predicted with the labeled status.

Result-set mismatches found while building the external benchmark are listed in `docs/DECISIONS.md` (REAL arithmetic
precision, LIMIT ties, seed insertion order); none is open.

## Definitions

| Metric | Definition |
|---|---|
| Problem detection | Is a guarantee flagged (CHANGED or BROKEN) at all. TP: labeled and predicted non-SAFE; FP: labeled SAFE, predicted non-SAFE; FN: labeled non-SAFE, predicted SAFE. |
| Per-class precision / recall / F1 | One-vs-rest over SAFE, CHANGED, BROKEN, from the confusion matrix. |
| Severity accuracy | Fraction of labeled guarantees whose predicted status equals the label. |
| Cohen's κ | (observed agreement − chance agreement) / (1 − chance agreement) between optimizer and advisor over EMBED/REFERENCE. |
| Result-set correctness | MATCH queries / verified queries, PostgreSQL as the oracle. |
| Mutation score | Killed mutants / (killed + survived + timed out), mutmut on `equivalence/` and `optimizer/`. |

## Limits
Labels in the self-labeled group were written by the author of the rules. The corpus is small, schemas are mostly
synthetic, workloads are hand-written, and the real-AI agreement needs an API key (`schemashift corpus report --ai`).
