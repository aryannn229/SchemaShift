"""Incremental schema migration compiler (Phase 22). Pure: no database access."""

from schemashift.evolve.alter import apply_alters
from schemashift.evolve.differ import diff_schemas
from schemashift.evolve.model import EvolutionPlan, MigrationStep, SchemaDiff
from schemashift.evolve.plan import evolve, plan_evolution
from schemashift.evolve.script import render_script

__all__ = [
    "EvolutionPlan",
    "MigrationStep",
    "SchemaDiff",
    "apply_alters",
    "diff_schemas",
    "evolve",
    "plan_evolution",
    "render_script",
]
