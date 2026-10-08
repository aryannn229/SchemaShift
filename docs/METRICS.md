# Metrics

Computed by `schemashift evaluate` over the labeled corpus in `backend/tests/corpus/`.

- **Date:** 2026-10-08
- **Commit:** 30f85d8

## Equivalence checker

```
corpus schemas        : 32
labeled nodes         : 883
detection rate        : 100.0%  (TP=225 FN=0)
false positive rate   : 0.0%  (FP=0 TN=658)
severity accuracy     : 100.0%
confusion matrix (rows = label, cols = predicted):
                 SAFE  CHANGED   BROKEN
  SAFE            658        0        0
  CHANGED           0      196        0
  BROKEN            0        0       29
```

## System metrics (bundled samples)

- **Result-set correctness:** 100.0% (21/21 queries MATCH between PostgreSQL and MongoDB on the ecommerce, blog, university and banking samples).
- **AI agreement:** not measured (needs `ANTHROPIC_API_KEY`; run `evaluate --ai`). The mock advisor never counts toward this metric.

## Definitions

| Metric | Definition |
|---|---|
| Detection rate | TP / (TP + FN). Positives are nodes labeled CHANGED or BROKEN; TP = predicted non-SAFE with the correct severity. |
| False positive rate | FP / (FP + TN). FP = node labeled SAFE but predicted CHANGED/BROKEN. |
| Severity accuracy | Fraction of labeled nodes whose predicted status equals the label. |
| Result-set correctness | MATCH queries / total verified queries (Phase 7). |
| AI agreement rate | Placements where AI == optimizer / placements with AI available (Phase 8). |

## How the labels were made, and their limits

Every corpus schema has an `expected.yaml` with a hand-written label and a one-line justification
per guarantee node (mechanical `type:*` / `not_null:*` nodes may be covered by explicit bulk
patterns). Labels were written from MongoDB semantics and the spec's rule table, **by the same
author as the rules**, so agreement is not independent validation: it shows the implementation is
internally consistent with the documented semantics. The corpus tests also check that the metric
is sensitive (deliberately broken rules make detection/FPR fail), that every non-mechanical node
is labeled, and that expected diagnostics match exactly.
