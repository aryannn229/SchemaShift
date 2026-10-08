from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from schemashift.api.db.models import Base
from schemashift.api.deps import get_state
from schemashift.api.main import create_app
from schemashift.api.middleware import RateLimiter
from schemashift.api.settings import Settings

SCHEMA = """
CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE);
CREATE TABLE orders (id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id), total NUMERIC(10,2));
"""
QUERIES = (
    "SELECT u.email, o.total FROM users u JOIN orders o ON o.user_id = u.id WHERE o.total > 10;"
)


def make_client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        app_database_url="sqlite://",
        environment="test",
        samples_dir=str(Path(__file__).resolve().parents[4] / "samples"),
        metrics_file=str(tmp_path / "metrics.json"),
        **overrides,  # type: ignore[arg-type]
    )
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as c:
        yield c


def wait_done(client: TestClient, run_id: str) -> dict[str, object]:
    get_state_for = client.app.state.services  # type: ignore[attr-defined]
    get_state_for.wait(run_id)
    res = client.get(f"/api/v1/runs/{run_id}")
    assert res.status_code == 200
    return res.json()  # type: ignore[no-any-return]


def test_compile_returns_full_report(client: TestClient) -> None:
    res = client.post("/api/v1/compile", json={"schema_sql": SCHEMA, "queries_sql": QUERIES})
    assert res.status_code == 200
    body = res.json()
    assert body["overall_verdict"] in {"SAFE", "CHANGED", "BROKEN"}
    assert {n["id"] for n in body["graph_nodes"]} == {"users", "orders"}
    assert body["graph_edges"][0]["child"] == "orders"
    assert body["queries"][0]["mongosh"]
    assert any(f["purpose"] == "migration" for f in body["generated"])
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-request-id"]


def test_compile_reports_syntax_errors_as_diagnostics(client: TestClient) -> None:
    res = client.post("/api/v1/compile", json={"schema_sql": "CREATE TABLE ("})
    assert res.status_code == 200
    assert res.json()["has_errors"] is True


def test_compile_rejects_unknown_fields_types(client: TestClient) -> None:
    assert client.post("/api/v1/compile", json={}).status_code == 422
    assert client.post("/api/v1/compile", json={"schema_sql": 5}).status_code == 422


def test_table_limit_enforced(tmp_path: Path) -> None:
    with make_client(tmp_path, max_tables=1) as c:
        res = c.post("/api/v1/compile", json={"schema_sql": SCHEMA})
        assert res.status_code == 422
        assert "tables" in res.json()["detail"]


def test_body_limit(tmp_path: Path) -> None:
    with make_client(tmp_path, max_body_bytes=500) as c:
        res = c.post("/api/v1/compile", json={"schema_sql": "x" * 1000})
        assert res.status_code == 413


def test_rate_limit(tmp_path: Path) -> None:
    with make_client(tmp_path, compile_rate_per_minute=2) as c:
        codes = [
            c.post("/api/v1/compile", json={"schema_sql": SCHEMA}).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]


def test_rate_limiter_window() -> None:
    lim = RateLimiter({"compile": 1}, window=10)
    assert lim.allow("a", "compile", now=0)
    assert not lim.allow("a", "compile", now=5)
    assert lim.allow("b", "compile", now=5)
    assert lim.allow("a", "compile", now=11)
    assert lim.allow("a", "other", now=0)


def test_run_lifecycle_and_export(client: TestClient) -> None:
    res = client.post("/api/v1/runs", json={"schema_sql": SCHEMA, "queries_sql": QUERIES})
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    detail = wait_done(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] is not None
    assert detail["overall_verdict"] == detail["result"]["overall_verdict"]  # type: ignore[index]

    page = client.get("/api/v1/runs").json()
    assert page["total"] == 1 and page["items"][0]["id"] == run_id

    z = client.get(f"/api/v1/runs/{run_id}/export")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "report.json" in names and "report.md" in names and "schema.sql" in names
    assert any(n.startswith("code/") for n in names)

    j = client.get(f"/api/v1/runs/{run_id}/export", params={"format": "json"})
    assert j.json()["id"] == run_id
    assert client.get(f"/api/v1/runs/{run_id}/export", params={"format": "tar"}).status_code == 422


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/runs/nope").status_code == 404
    assert client.get("/api/v1/runs/nope/export").status_code == 404


def test_verify_requires_configuration(client: TestClient) -> None:
    res = client.post("/api/v1/runs", json={"schema_sql": SCHEMA, "verify": True})
    assert res.status_code == 503


def test_samples(client: TestClient) -> None:
    names = {s["name"] for s in client.get("/api/v1/samples").json()}
    assert {"ecommerce", "blog", "university", "banking"} <= names
    banking = client.get("/api/v1/samples/banking").json()
    assert "CREATE TABLE" in banking["schema_sql"]
    assert "m2n:beneficiaries" in banking["options"]["relationship_overrides"]
    assert client.get("/api/v1/samples/..%2Fsecret").status_code == 404


def test_sample_compiles(client: TestClient) -> None:
    sample = client.get("/api/v1/samples/ecommerce").json()
    res = client.post(
        "/api/v1/compile",
        json={"schema_sql": sample["schema_sql"], "queries_sql": sample["queries_sql"]},
    )
    assert res.status_code == 200 and not res.json()["has_errors"]


def test_metrics_missing_then_present(tmp_path: Path) -> None:
    with make_client(tmp_path) as c:
        assert c.get("/api/v1/metrics").json() == {"available": False, "data": None}
        (tmp_path / "metrics.json").write_text('{"detection": 1.0}')
        assert c.get("/api/v1/metrics").json()["data"] == {"detection": 1.0}


def test_health_reports_app_database(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["database"] == "up" and body["status"] == "ok"


def test_ai_cache_roundtrip(client: TestClient) -> None:
    client.get("/api/v1/health")
    state = client.app.state.services  # type: ignore[attr-defined]
    assert state.cache.get("k") is None
    state.cache.put("k", {"a": 1})
    state.cache.put("k", {"a": 2})
    assert state.cache.get("k") == {"a": 2}


def test_openapi_documents_every_route(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    expected = {
        "/api/v1/health",
        "/api/v1/compile",
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/export",
        "/api/v1/samples",
        "/api/v1/samples/{name}",
        "/api/v1/metrics",
    }
    assert expected <= set(spec["paths"])


def test_get_state_is_lazy_singleton(tmp_path: Path) -> None:
    app = create_app(Settings(app_database_url="sqlite://"))
    assert not hasattr(app.state, "services")
    with TestClient(app) as c:
        c.get("/api/v1/health")
        first = app.state.services
        c.get("/api/v1/health")
        assert app.state.services is first
    assert get_state  # imported for the dependency contract


def test_migrations_match_models(tmp_path: Path) -> None:
    from alembic.config import Config

    from alembic import command

    backend = Path(__file__).resolve().parents[3]
    db = tmp_path / "mig.db"
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    import os

    os.environ["APP_DATABASE_URL"] = f"sqlite:///{db}"
    try:
        command.upgrade(cfg, "head")
    finally:
        del os.environ["APP_DATABASE_URL"]
    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert set(Base.metadata.tables) <= tables


def test_time_budget_of_compile(client: TestClient) -> None:
    start = time.perf_counter()
    client.post("/api/v1/compile", json={"schema_sql": SCHEMA, "queries_sql": QUERIES})
    assert time.perf_counter() - start < 10
