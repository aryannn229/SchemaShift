"""Shared fixtures. Integration tests need the services of docker-compose.test.yml.

Start them with ``docker compose -f docker-compose.test.yml up -d`` (Mongo is initiated as a
single-node replica set by ``scripts/dev.ps1 test-integration``) and run ``pytest -m integration``.
Override the endpoints with TEST_MONGO_URL / TEST_PG_DSN.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

MONGO_URL = os.environ.get("TEST_MONGO_URL", "mongodb://localhost:27018/?directConnection=true")
PG_DSN = os.environ.get("TEST_PG_DSN", "postgresql://test:test@localhost:5433/test")


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[Any]:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    client: Any = MongoClient(MONGO_URL, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        pytest.skip(f"MongoDB not reachable at {MONGO_URL}")
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client: Any) -> Iterator[Any]:
    name = f"t_{uuid.uuid4().hex[:12]}"
    yield mongo_client[name]
    mongo_client.drop_database(name)


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    import psycopg

    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            pass
    except psycopg.Error:
        pytest.skip(f"PostgreSQL not reachable at {PG_DSN}")
    return PG_DSN


@pytest.fixture
def pg_schema(pg_dsn: str) -> Iterator[tuple[Any, str]]:
    """A fresh schema in the test database; yields (connection, schema name)."""
    import psycopg

    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')
        conn.execute(f'SET search_path TO "{name}"')
        yield conn, name
        conn.execute(f'DROP SCHEMA "{name}" CASCADE')
