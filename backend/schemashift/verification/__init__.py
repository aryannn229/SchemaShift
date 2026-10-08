"""Verification harness: runs SQL on PostgreSQL and generated code on MongoDB, then diffs."""

from schemashift.verification.diff import ResultDiff, RowDiff, compare
from schemashift.verification.runner import (
    ProbeOutcome,
    QueryOutcome,
    VerificationError,
    VerificationReport,
    verify,
)
from schemashift.verification.sandbox import Sandbox, SandboxConfig, sweep
from schemashift.verification.seed import SeedData, SeedError, generate_seed, seed_from_inserts

__all__ = [
    "ProbeOutcome",
    "QueryOutcome",
    "ResultDiff",
    "RowDiff",
    "Sandbox",
    "SandboxConfig",
    "SeedData",
    "SeedError",
    "VerificationError",
    "VerificationReport",
    "compare",
    "generate_seed",
    "seed_from_inserts",
    "sweep",
    "verify",
]
