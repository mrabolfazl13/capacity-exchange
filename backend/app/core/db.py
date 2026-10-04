"""Async SQLAlchemy engine/session plumbing.

`create_app` builds an engine from settings and stores it on `app.state`;
`get_session` is the FastAPI dependency yielding a transactional session
(commit on success, rollback on exception — services rely on it).
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def build_engine(settings: Settings, **kwargs) -> AsyncEngine:
    return create_async_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
        **kwargs,
    )


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, autocommit-on-2xx pattern."""
    maker: async_sessionmaker[AsyncSession] = get_sessionmaker(request)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
