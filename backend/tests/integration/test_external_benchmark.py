"""Public schemas against real PostgreSQL and MongoDB (no labels: PostgreSQL is the oracle)."""

from __future__ import annotations

import pytest

from schemashift.metrics.external import evaluate_external
from schemashift.verification.sandbox import SandboxConfig
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration


@pytest.fixture
def sandbox(pg_dsn: str, mongo_client: object) -> SandboxConfig:
    return SandboxConfig(pg_admin_dsn=PG_DSN, mongo_url=MONGO_URL)


def test_chinook_and_northwind_workloads_match(sandbox: SandboxConfig) -> None:
    data = evaluate_external(sandbox=sandbox)
    for name in ("chinook", "northwind"):
        rs = data["schemas"][name]["result_set"]
        assert rs["verified"] == data["schemas"][name]["queries"], name
        assert rs["mismatches"] == [], (name, rs["mismatches"])
    # schemas with unsupported constructs are reported, not verified
    for name in ("pagila", "supabase_slack_clone", "graphile_starter"):
        assert "skipped" in data["schemas"][name]["result_set"]
    assert data["result_set"]["rate"] == 1.0
    notes = [n["note"] for n in data["schemas"]["northwind"]["result_set"]["notes"]]
    assert any("REAL" in n for n in notes)  # float4 arithmetic allowance is recorded, not silent
