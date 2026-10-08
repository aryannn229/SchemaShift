"""Optimizer: decides embed vs reference for every relationship."""

from schemashift.optimizer.cost_model import CostResult, score_relationship
from schemashift.optimizer.features import RelationshipFeatures, extract_features
from schemashift.optimizer.planner import OptimizerOptions, plan_placement
from schemashift.optimizer.weights import Weights, load_weights
from schemashift.optimizer.workload import AccessHint

__all__ = [
    "AccessHint",
    "CostResult",
    "OptimizerOptions",
    "RelationshipFeatures",
    "Weights",
    "extract_features",
    "load_weights",
    "plan_placement",
    "score_relationship",
]
