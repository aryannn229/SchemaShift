"""Cost-based optimizer for generated aggregation pipelines (Phase 19). Pure: no database access."""

from schemashift.aggopt.cost import CostEstimate, Stats, estimate_cost
from schemashift.aggopt.rewrites import REWRITES, RewriteRule
from schemashift.aggopt.search import OptimizedPipeline, optimize_pipeline, recommend_indexes

__all__ = [
    "REWRITES",
    "CostEstimate",
    "OptimizedPipeline",
    "RewriteRule",
    "Stats",
    "estimate_cost",
    "optimize_pipeline",
    "recommend_indexes",
]
