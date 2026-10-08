"""Generated artifacts executed against a real MongoDB."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.unit.codegen.helpers import SAMPLE_NAMES, generate_sample

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]


def run_python(source: str, entry: str, db: Any) -> None:
    namespace: dict[str, Any] = {"__name__": "generated"}
    exec(compile(source, "<generated>", "exec"), namespace)  # noqa: S102 (our own generated code)
    namespace[entry](db)


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_generated_python_creates_collections_and_indexes(name: str, mongo_db: Any) -> None:
    _, out = generate_sample(name)
    run_python(out.file("python/schema.py").content, "create_collections", mongo_db)
    run_python(out.file("python/indexes.py").content, "create_indexes", mongo_db)
    # idempotent: running again updates validators instead of failing
    run_python(out.file("python/schema.py").content, "create_collections", mongo_db)
    run_python(out.file("python/indexes.py").content, "create_indexes", mongo_db)
    existing = set(mongo_db.list_collection_names())
    assert set(out.collections) <= existing
    for coll in out.collections:
        info = mongo_db.command("listCollections", filter={"name": coll})["cursor"]["firstBatch"][0]
        assert (
            "$jsonSchema" in str(info["options"]["validator"])
            and info["options"]["validationLevel"] == "strict"
        )


def docker_mongosh(script: str, db_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(REPO / "docker-compose.test.yml"),
            "exec",
            "-T",
            "mongo",
            "mongosh",
            "--quiet",
            f"mongodb://localhost:27017/{db_name}",
        ],
        input=script,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_generated_mongosh_loads(name: str, mongo_client: Any) -> None:
    probe = docker_mongosh("db.runCommand({ping: 1}).ok", "admin")
    if probe.returncode != 0:
        pytest.skip("mongosh via docker compose is not available")
    _, out = generate_sample(name)
    db_name = f"t_mongosh_{name}"
    try:
        for path in ("mongosh/01_collections.js", "mongosh/02_indexes.js"):
            result = docker_mongosh(out.file(path).content, db_name)
            assert result.returncode == 0, result.stderr + result.stdout
            assert "Error" not in result.stderr + result.stdout, result.stderr + result.stdout
        assert set(out.collections) <= set(mongo_client[db_name].list_collection_names())
    finally:
        mongo_client.drop_database(db_name)
