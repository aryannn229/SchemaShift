"""Engine / session factory and the database-backed advisor cache."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from schemashift.api.db.models import AICacheRow, Base


def make_engine(url: str) -> Engine:
    """SQLite (tests / development) shares one connection across threads; PostgreSQL pools."""
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class DbCache:
    """``AdvisorCache`` backed by the ``ai_cache`` table (shared between workers and restarts)."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._lock = threading.Lock()  # advisors call the cache from worker threads

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock, session_scope(self._factory) as s:
            row = s.scalar(select(AICacheRow).where(AICacheRow.key == key))
            return dict(row.response) if row else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock, session_scope(self._factory) as s:
            row = s.get(AICacheRow, key)
            if row is None:
                s.add(AICacheRow(key=key, response=value))
            else:
                row.response = value
