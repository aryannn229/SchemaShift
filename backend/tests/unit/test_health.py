from fastapi.testclient import TestClient

from schemashift.api.main import app

client = TestClient(app)


def test_health_ok_without_dependencies() -> None:
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["postgres"] == "unconfigured"
    assert body["mongo"] == "unconfigured"


def test_openapi_served() -> None:
    assert client.get("/api/openapi.json").status_code == 200
