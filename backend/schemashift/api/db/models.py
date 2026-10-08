"""SQLAlchemy models of the application database (SPEC section 10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    """Created now, unused until the auth roadmap item."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending/running/done/failed
    schema_sql: Mapped[str] = mapped_column(Text)
    queries_sql: Mapped[str] = mapped_column(Text, default="")
    seed_sql: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    overall_verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)
    counts: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    seed: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    verify: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        Json, nullable=True
    )  # full compile response
    verification: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)

    verdicts: Mapped[list[VerdictRow]] = relationship(
        cascade="all, delete-orphan", back_populates="run"
    )
    placements: Mapped[list[PlacementRow]] = relationship(
        cascade="all, delete-orphan", back_populates="run"
    )
    query_results: Mapped[list[QueryResultRow]] = relationship(
        cascade="all, delete-orphan", back_populates="run"
    )


class VerdictRow(Base):
    __tablename__ = "verdicts"
    __table_args__ = (Index("ix_verdicts_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String(512))
    rule_id: Mapped[str] = mapped_column(String(64))
    ir_node_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    mitigation: Mapped[str | None] = mapped_column(Text, nullable=True)
    conditions: Mapped[list[Any]] = mapped_column(Json, default=list)
    source_span: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)

    run: Mapped[Run] = relationship(back_populates="verdicts")


class PlacementRow(Base):
    __tablename__ = "placements"
    __table_args__ = (Index("ix_placements_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    relationship_id: Mapped[str] = mapped_column(String(512))
    decision: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    top_factors: Mapped[list[Any]] = mapped_column(Json, default=list)
    ai_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    agree: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    run: Mapped[Run] = relationship(back_populates="placements")


class QueryResultRow(Base):
    __tablename__ = "query_results"
    __table_args__ = (Index("ix_query_results_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    query_id: Mapped[str] = mapped_column(String(32))
    sql: Mapped[str] = mapped_column(Text)
    pipeline: Mapped[Any] = mapped_column(Json, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    pg_rows: Mapped[int] = mapped_column(Integer, default=0)
    mongo_rows: Mapped[int] = mapped_column(Integer, default=0)
    diff: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)

    run: Mapped[Run] = relationship(back_populates="query_results")


class AICacheRow(Base):
    __tablename__ = "ai_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
