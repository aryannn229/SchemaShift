"""Ephemeral sandboxes: one restricted PostgreSQL role+schema and one MongoDB database per run.

Isolation: the PostgreSQL role is NOSUPERUSER/NOCREATEDB/NOCREATEROLE, owns only its schema,
has ``search_path`` locked to it, a 5 s ``statement_timeout`` and a small connection limit.
Everything is dropped in ``close()`` (and by ``sweep()`` for crashed runs older than one hour).
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from types import TracebackType
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pymongo import MongoClient

from schemashift.models.base import FrozenModel

MARKER = "schemashift-sandbox:"
NAME_RE = re.compile(r"^run_[0-9a-f]{12}$")


class SandboxConfig(FrozenModel):
    pg_admin_dsn: str  # a role allowed to CREATE ROLE / SCHEMA (deployment-level)
    mongo_url: str
    mongo_sandbox_url: str | None = None  # readWrite on run_* only (falls back to mongo_url)
    statement_timeout_ms: int = 5000
    idle_in_transaction_timeout_ms: int = 10000
    connection_limit: int = 4
    max_age_seconds: int = 3600


class Sandbox:
    """Use as a context manager; resources are released even when the body raises."""

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self.run_id = uuid.uuid4().hex[:12]
        self.name = f"run_{self.run_id}"
        self._password = secrets.token_urlsafe(18)
        self.pg: Any = None  # connection as the restricted role (autocommit)
        self._mongo: MongoClient[Any] | None = None
        self.mongo_db: Any = None
        self._created = False

    # ---------------------------------------------------------------- lifecycle
    def __enter__(self) -> Sandbox:
        self.create()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def create(self) -> None:
        c = self.config
        role = sql.Identifier(self.name)
        try:
            with psycopg.connect(c.pg_admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION CONNECTION LIMIT {}"
                    ).format(role, sql.Literal(self._password), sql.Literal(c.connection_limit))
                )
                self._created = True
                admin.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(role, role))
                admin.execute(
                    sql.SQL("COMMENT ON SCHEMA {} IS {}").format(
                        role, sql.Literal(f"{MARKER}{int(time.time())}")
                    )
                )
                for setting, value in (
                    ("search_path", self.name),
                    ("statement_timeout", f"{c.statement_timeout_ms}ms"),
                    (
                        "idle_in_transaction_session_timeout",
                        f"{c.idle_in_transaction_timeout_ms}ms",
                    ),
                ):
                    admin.execute(
                        sql.SQL("ALTER ROLE {} SET {} = {}").format(
                            role, sql.Identifier(setting), sql.Literal(value)
                        )
                    )
            self.pg = psycopg.connect(
                make_conninfo(c.pg_admin_dsn, user=self.name, password=self._password),
                autocommit=True,
            )
            self._mongo = MongoClient(
                c.mongo_sandbox_url or c.mongo_url, serverSelectionTimeoutMS=3000
            )
            self.mongo_db = self._mongo[self.name]
            self.mongo_db["_sandbox"].insert_one({"_id": "marker", "created_at": time.time()})
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Drop everything this sandbox created; never raises."""
        try:
            if self.pg is not None:
                self.pg.close()
        except psycopg.Error:
            pass
        self.pg = None
        if self._created:
            try:
                with psycopg.connect(self.config.pg_admin_dsn, autocommit=True) as admin:
                    drop_role_and_schema(admin, self.name)
            except psycopg.Error:
                pass
            self._created = False
        try:
            if self._mongo is not None:
                self._mongo.drop_database(self.name)
                self._mongo.close()
        except Exception:  # noqa: BLE001 (best effort cleanup)
            pass
        self._mongo = None
        self.mongo_db = None


def drop_role_and_schema(admin: Any, name: str) -> None:
    ident = sql.Identifier(name)
    admin.execute(
        sql.SQL("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = {}").format(
            sql.Literal(name)
        )
    )
    admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
    admin.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(ident)) if _role_exists(
        admin, name
    ) else None
    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident))


def _role_exists(admin: Any, name: str) -> bool:
    row = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    return row is not None


def sweep(config: SandboxConfig, now: float | None = None) -> dict[str, list[str]]:
    """Janitor: drop sandboxes older than ``max_age_seconds`` (left behind by crashed runs)."""
    now = time.time() if now is None else now
    dropped: dict[str, list[str]] = {"postgres": [], "mongo": []}
    with psycopg.connect(config.pg_admin_dsn, autocommit=True) as admin:
        rows = admin.execute(
            "SELECT nspname, obj_description(oid, 'pg_namespace') FROM pg_namespace "
            "WHERE nspname ~ '^run_[0-9a-f]{12}$'"
        ).fetchall()
        known = set()
        for name, comment in rows:
            known.add(name)
            created = _marker_time(comment)
            if created is None or now - created > config.max_age_seconds:
                drop_role_and_schema(admin, name)
                dropped["postgres"].append(name)
        orphans = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname ~ '^run_[0-9a-f]{12}$'"
        ).fetchall()
        for (name,) in orphans:
            if name not in known:
                drop_role_and_schema(admin, name)
                dropped["postgres"].append(name)
    client: MongoClient[Any] = MongoClient(config.mongo_url, serverSelectionTimeoutMS=3000)
    try:
        for db_name in client.list_database_names():
            if not NAME_RE.match(db_name):
                continue
            marker = client[db_name]["_sandbox"].find_one({"_id": "marker"})
            created_at = float(marker["created_at"]) if marker else None
            if created_at is None or now - created_at > config.max_age_seconds:
                client.drop_database(db_name)
                dropped["mongo"].append(db_name)
    finally:
        client.close()
    return dropped


def _marker_time(comment: str | None) -> float | None:
    if comment and comment.startswith(MARKER):
        try:
            return float(comment[len(MARKER) :])
        except ValueError:
            return None
    return None
