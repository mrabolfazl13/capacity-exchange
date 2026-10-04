"""dev_db.py — Docker-free local PostgreSQL for development and tests (pgserver).

Starts the bundled Postgres 16 build on localhost:5544 (the port CONTRACTS §11 pins for
local dev), ensures the `capacity` role plus the `capacity`/`capacity_test` databases, then
stays in the foreground so a supervisor script can own its lifetime.

    python backend/scripts/dev_db.py            # start and hold
    python backend/scripts/dev_db.py --stop     # stop a previously started cluster

pgserver picks an ephemeral port by default; forcing 5544 keeps DATABASE_URL, docker-compose
and the dev scripts agreeing on one address.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pgserver
import pgserver.postgres_server as _ps

DEV_PORT = 5544
DEV_ROLE = "capacity"
DEV_PASSWORD = "capacity"
DEV_DATABASES = ("capacity", "capacity_test")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PGDATA = BACKEND_ROOT / ".run" / "pgdata"


def _force_port(port: int) -> None:
    def _find_suitable_port(address=None):  # noqa: ANN001, ANN001
        return port

    _ps.find_suitable_port = _find_suitable_port


def _ensure_role_and_databases(srv: pgserver.PostgresServer) -> None:
    r"""Idempotent bootstrap; \gexec only executes the statement the SELECT returns."""
    srv.psql(
        "SELECT 'CREATE ROLE capacity LOGIN PASSWORD ''%s'' CREATEDB' "
        "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'capacity')" % DEV_PASSWORD
        + " \\gexec"
    )
    for db in DEV_DATABASES:
        srv.psql(
            f"SELECT 'CREATE DATABASE {db} OWNER {DEV_ROLE}' "
            f"WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{db}') \\gexec"
        )
    # Database-level CREATE/USAGE rights are needed by `alembic upgrade head`.
    grants = "".join(f"\\connect {db}\nGRANT CREATE, USAGE ON SCHEMA public TO PUBLIC;\n"
                     for db in DEV_DATABASES)
    srv.psql(grants)


def uri(srv: pgserver.PostgresServer, database: str) -> str:
    return srv.get_uri(database)


def stop() -> int:
    if not PGDATA.exists():
        print(f"[dev-db] no cluster at {PGDATA}")
        return 0
    srv = pgserver.get_server(PGDATA, cleanup_mode="stop")
    srv.cleanup()
    print(f"[dev-db] stopped cluster at {PGDATA}")
    return 0


def start(reset: bool = False) -> int:
    if reset and PGDATA.exists():
        shutil.rmtree(PGDATA, ignore_errors=True)
    _force_port(DEV_PORT)
    PGDATA.mkdir(parents=True, exist_ok=True)
    srv = pgserver.get_server(PGDATA, cleanup_mode="stop")
    _ensure_role_and_databases(srv)
    print(f"[dev-db] postgres listening on 127.0.0.1:{DEV_PORT}", flush=True)
    for db in DEV_DATABASES:
        print(f"[dev-db]   postgresql://{DEV_ROLE}@127.0.0.1:{DEV_PORT}/{db}", flush=True)

    import signal
    import time

    stopping = False

    def _handle(signum, frame):  # noqa: ANN001
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGINT)):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            pass

    try:
        while not stopping:
            time.sleep(1)
    finally:
        print("[dev-db] stopping", flush=True)
        srv.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true", help="stop the local cluster and exit")
    parser.add_argument("--reset", action="store_true", help="delete the data directory first")
    args = parser.parse_args(argv)
    return stop() if args.stop else start(reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
