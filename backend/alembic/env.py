"""Alembic environment: the database URL comes from APP_DATABASE_URL (or -x url=...)."""

import os

from sqlalchemy import engine_from_config, pool

from alembic import context
from schemashift.api.db.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get(
        "APP_DATABASE_URL", ""
    )
    if not url:
        raise RuntimeError("set APP_DATABASE_URL (or pass -x url=...) to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
