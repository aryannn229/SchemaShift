"""All configuration comes from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # databases
    app_database_url: str = ""  # empty -> a local SQLite file (development only)
    mongo_url: str = ""
    sandbox_admin_database_url: str = ""
    mongo_sandbox_url: str = ""
    db_auto_create: bool = True  # create tables on start (production runs Alembic instead)

    # web
    cors_origins: str = "http://localhost:5173"
    environment: str = "development"
    log_level: str = "INFO"
    sentry_dsn: str = ""

    # limits (SPEC section 9 / 11)
    max_body_bytes: int = 200_000
    max_tables: int = 50
    max_queries: int = 30
    compile_rate_per_minute: int = 30
    verify_rate_per_minute: int = 5

    # AI
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"

    # files
    samples_dir: str = str(REPO_ROOT / "samples")
    metrics_file: str = str(REPO_ROOT / "docs" / "metrics.json")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return self.app_database_url or f"sqlite:///{REPO_ROOT / 'schemashift_dev.db'}"

    @property
    def verification_enabled(self) -> bool:
        return bool(self.sandbox_admin_database_url and self.mongo_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
