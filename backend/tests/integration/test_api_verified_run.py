from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from schemashift.api.main import create_app
from schemashift.api.settings import Settings
from tests.conftest import MONGO_URL, PG_DSN

pytestmark = pytest.mark.integration

SCHEMA = """
CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);
CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id), total NUMERIC(10,2));
"""
QUERIES = (
    "SELECT u.email, o.total FROM users u JOIN orders o ON o.user_id = u.id WHERE o.total > 10;"
)


def test_verified_run_end_to_end(mongo_client: object, pg_dsn: str, tmp_path: Path) -> None:
    settings = Settings(
        app_database_url="sqlite://",
        sandbox_admin_database_url=PG_DSN,
        mongo_url=MONGO_URL,
        samples_dir=str(Path(__file__).resolve().parents[3] / "samples"),
    )
    with TestClient(create_app(settings)) as c:
        res = c.post(
            "/api/v1/runs",
            json={"schema_sql": SCHEMA, "queries_sql": QUERIES, "verify": True, "seed": 1},
        )
        assert res.status_code == 202
        run_id = res.json()["run_id"]
        c.app.state.services.wait(run_id, timeout=120)
        detail = c.get(f"/api/v1/runs/{run_id}").json()
        assert detail["status"] == "done", detail["error"]
        assert detail["verification"]["queries"][0]["status"] == "MATCH"


def test_replaying_a_counterexample_through_the_api(
    mongo_client: object, pg_dsn: str, tmp_path: Path
) -> None:
    settings = Settings(
        app_database_url="sqlite://",
        sandbox_admin_database_url=PG_DSN,
        mongo_url=MONGO_URL,
        samples_dir=str(Path(__file__).resolve().parents[3] / "samples"),
    )
    schema = (
        "CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);"
        "CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) "
        "ON DELETE CASCADE, total NUMERIC(10,2));"
    )
    overrides = {"relationship_overrides": {"fk:orders.user_id->users.id": "REFERENCE"}}
    with TestClient(create_app(settings)) as c:
        run_id = c.post(
            "/api/v1/runs",
            json={"schema_sql": schema, "verify": True, "seed": 1, "options": overrides},
        ).json()["run_id"]
        c.app.state.services.wait(run_id, timeout=120)
        detail = c.get(f"/api/v1/runs/{run_id}").json()
        assert detail["status"] == "done", detail["error"]
        stored = {x["verdict_id"] for x in detail["verification"]["counterexamples"]}
        assert "cascade_delete:orders.user_id->users.id" in stored
        res = c.post(
            f"/api/v1/runs/{run_id}/counterexamples/replay",
            params={"verdict_id": "cascade_delete:orders.user_id->users.id"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["gap"] is None and body["counterexample"]["steps"][0]["sql"].startswith(
            "DELETE"
        )
        assert body["counterexample"] == next(
            x
            for x in detail["verification"]["counterexamples"]
            if x["verdict_id"] == body["verdict_id"]
        )  # same seed, same minimal counterexample
        gap = c.post(
            f"/api/v1/runs/{run_id}/counterexamples/replay",
            params={
                "verdict_id": next(
                    v["node_id"]
                    for v in detail["result"]["verdicts"]
                    if v["rule_id"] == "EQ-AUTOINC"
                )
            },
        ).json()
        assert gap["counterexample"] is None and "ObjectId" in gap["gap"]["reason"]
