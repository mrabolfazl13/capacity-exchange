"""Backend test harness (CONTRACTS §13).

Tests run against a real PostgreSQL 16 (pgserver on :5544) with the `capacity_test`
database and the schema built by `alembic upgrade head` — never SQLite, because the
booking engine depends on advisory locks, native enums and generated columns.

If no server is listening the harness starts one itself, so `pytest -q` works offline on a
fresh clone.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.eventloop import use_selector_event_loop  # noqa: E402

use_selector_event_loop()

DEV_HOST, DEV_PORT = "127.0.0.1", 5544
TEST_DB = "capacity_test"
TEST_ROLE = os.environ.get("TEST_DB_USER", "capacity")
TEST_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "capacity")


def test_database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        f"postgresql+psycopg://{TEST_ROLE}:{TEST_PASSWORD}@{DEV_HOST}:{DEV_PORT}/{TEST_DB}")


def _port_open(port: int, host: str = DEV_HOST) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Ensure a live cluster with the test database, then hand back its async DSN."""
    if not _port_open(DEV_PORT):
        import pgserver
        import pgserver.postgres_server as _ps

        def _fixed_port(address=None):  # noqa: ANN001
            return DEV_PORT

        _ps.find_suitable_port = _fixed_port
        pgdata = BACKEND_ROOT / ".run" / "pgdata"
        pgdata.mkdir(parents=True, exist_ok=True)
        srv = pgserver.get_server(pgdata, cleanup_mode="stop")
        sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
        from scripts.dev_db import _ensure_role_and_databases

        _ensure_role_and_databases(srv)
    _recreate_schema(test_database_url())
    _upgrade_head(test_database_url())
    return test_database_url()


def _sync_dsn(async_dsn: str) -> str:
    parsed = urlparse(async_dsn.replace("+psycopg", ""))
    return f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}{parsed.path}"


def _recreate_schema(dsn: str) -> None:
    """Drop and rebuild `public` so each session starts from an empty, grantable schema."""
    import psycopg

    target = urlparse(dsn).path.lstrip("/")
    admin = urlparse(_sync_dsn(dsn))._replace(path="/postgres").geturl()
    with psycopg.connect(admin, autocommit=True) as conn:
        if target not in [r[0] for r in conn.execute("SELECT datname FROM pg_database")]:
            conn.execute(f'CREATE DATABASE "{target}"')
    with psycopg.connect(_sync_dsn(dsn), autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT ALL ON SCHEMA public TO CURRENT_USER")
        conn.execute("GRANT CREATE, USAGE ON SCHEMA public TO PUBLIC")


def _upgrade_head(dsn: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", dsn)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def settings(pg_url: str):
    from app.core.config import Settings

    return Settings(
        database_url=pg_url,
        secret_key="test-secret-key-0123456789abcdef-0123456789abcdef",
        app_env="test",
        celery_mode="local",
        redis_url="",
        webhook_secret="test-webhook-secret",
        rate_limit_per_min=100000,
    )


@pytest.fixture(scope="session")
def app(settings):
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
async def client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture
async def db(app, client):
    """A session on the app's own engine, for direct assertions and seeding."""
    maker = app.state.sessionmaker
    async with maker() as session:
        yield session
        await session.rollback()


@pytest.fixture(autouse=True)
def clean_db(pg_url: str):
    """TRUNCATE everything between tests: FK-cascade with identities restarted."""
    import psycopg

    tables = []
    dsn = _sync_dsn(pg_url)
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename <> 'alembic_version'").fetchall()
        tables = [r[0] for r in rows]
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            conn.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
    yield
