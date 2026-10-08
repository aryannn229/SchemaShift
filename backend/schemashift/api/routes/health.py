"""Liveness plus dependency connectivity."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from schemashift.api.settings import Settings, get_settings

router = APIRouter()

DepStatus = Literal["up", "down", "unconfigured"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: DepStatus
    mongo: DepStatus


def _check_postgres(url: str) -> DepStatus:
    if not url:
        return "unconfigured"
    try:
        import psycopg

        dsn = url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return "up"
    except Exception:
        return "down"


def _check_mongo(url: str) -> DepStatus:
    if not url:
        return "unconfigured"
    try:
        from pymongo import MongoClient

        client: MongoClient[dict[str, object]] = MongoClient(url, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        client.close()
        return "up"
    except Exception:
        return "down"


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    pg = _check_postgres(settings.app_database_url)
    mongo = _check_mongo(settings.mongo_url)
    degraded = "down" in (pg, mongo)
    return HealthResponse(status="degraded" if degraded else "ok", postgres=pg, mongo=mongo)
