"""Alembic environment — async engine, metadata from app.models, DSN from DATABASE_URL.

Schema is authored by hand (not autogenerate) so every object is PG16-core-safe:
no contrib extensions (btree_gist/pgcrypto/citext) are required to upgrade.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings_cached  # noqa: E402
from app.core.eventloop import use_selector_event_loop  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Objects the migration owns and the model deliberately does not declare.
#: `offers.search_vector` is a GENERATED tsvector column with a GIN index (see the comment in
#: app/models/marketplace.py); SQLAlchemy cannot round-trip a computed PG column, so
#: autogenerate reads its absence in the model as a DROP. Excluding exactly these two names is
#: what lets `alembic check` run as a real drift gate in CI instead of failing on a phantom.
MIGRATION_OWNED = frozenset({("search_vector", "column"), ("ix_offers_search_vector", "index")})


def _include_object(object, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    return (name, type_) not in MIGRATION_OWNED


def _database_url() -> str:
    """Precedence: programmatic `-x`/config url > DATABASE_URL > app settings default.

    Tests call `command.upgrade(cfg, "head")` with `sqlalchemy.url` set to the test
    database; ignoring it would silently migrate the developer's own database instead.
    """
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        from app.core.config import Settings

        return Settings(database_url=configured).sqlalchemy_database_url
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        settings = get_settings_cached().model_copy(update={"database_url": env_url})
    else:
        settings = get_settings_cached()
    return settings.sqlalchemy_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    use_selector_event_loop()
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
