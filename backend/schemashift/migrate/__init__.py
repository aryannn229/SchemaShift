"""Safe migration mode (Phase 23): dry run, validation, checkpoints, manifest and consistency
checks."""

from schemashift.migrate.manifest import Manifest, load, save
from schemashift.migrate.runtime import (
    CheckResult,
    DryRunReport,
    Job,
    LossyStepsNeedConsent,
    MigrationError,
    dry_run,
    plan_preview,
    rollback_script,
    run,
    run_script,
    status,
    verify,
)

__all__ = [
    "CheckResult",
    "DryRunReport",
    "Job",
    "LossyStepsNeedConsent",
    "Manifest",
    "MigrationError",
    "dry_run",
    "load",
    "plan_preview",
    "rollback_script",
    "run",
    "run_script",
    "save",
    "status",
    "verify",
]
