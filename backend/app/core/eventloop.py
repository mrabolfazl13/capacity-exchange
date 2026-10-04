"""psycopg3 in async mode refuses Windows' default ProactorEventLoop.

Every entry point that drives an async engine on Windows (uvicorn's `asyncio.run`,
Alembic's online mode, pytest-asyncio) must call this first.
"""
from __future__ import annotations

import asyncio
import sys


def use_selector_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
