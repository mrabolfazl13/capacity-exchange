"""FastAPI application factory.

Wiring order matters: the request-id middleware is added last so it runs outermost and
every other layer (including the 429 envelope) can reference the same id (§2).
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import health
from app.api.v1 import router as v1_router
from app.core.config import Settings
from app.core.db import build_engine, build_sessionmaker
from app.core.errors import register_error_handlers
from app.core.ratelimit import RateLimiter, RateLimitMiddleware
from jobs.local_runner import LocalJobRunner

logger = logging.getLogger("capacityexchange.app")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Reuse an inbound X-Request-Id when present so client and server logs join up."""

    async def dispatch(self, request: Request,
                       call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-Id")
        request_id = (incoming or uuid.uuid4().hex)[:64]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(settings)
        app.state.engine = engine
        app.state.settings = settings
        app.state.sessionmaker = build_sessionmaker(engine)
        app.state.ratelimiter = RateLimiter(settings)
        runner = LocalJobRunner(app.state.sessionmaker, settings)
        runner.start()
        app.state.job_runner = runner
        logger.info("backend ready env=%s db=%s celery_mode=%s",
                    settings.app_env, _safe_dsn(settings), settings.celery_mode)
        try:
            yield
        finally:
            await runner.stop()
            await app.state.ratelimiter.close()
            await engine.dispose()

    app = FastAPI(
        title="Capacity Exchange API",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings

    # `get_settings_dep` reads app.state.settings, and tests build their own app instance.
    register_error_handlers(app)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-Id"])
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(v1_router.router, prefix="/api/v1")
    return app


def _safe_dsn(settings: Settings) -> str:
    """Log host/db only — credentials never go to stdout."""
    url = settings.sqlalchemy_database_url
    return url.split("@")[-1] if "@" in url else url


app = create_app()
