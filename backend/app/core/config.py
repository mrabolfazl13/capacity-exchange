"""Application settings — every CONTRACTS §11 variable via pydantic-settings.

Access pattern: `Settings()` is built once in `app.main.create_app` (or passed
explicitly in tests) and stored on `app.state.settings`; request code reads it via
`get_settings` dependency so tests can override per-app without global caches.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- CONTRACTS §11 ---
    database_url: str = Field(
        default="postgresql+psycopg://capacity:capacity@localhost:5544/capacity",
        description="SQLAlchemy async DSN. postgresql:// is auto-normalized to postgresql+psycopg://.",
    )
    secret_key: str = Field(default="dev-secret-change-me")
    access_token_ttl_min: int = Field(default=30)
    refresh_token_ttl_days: int = Field(default=14)
    redis_url: str = Field(default="")  # empty => DB-only behaviour (never 500 because Redis is down)
    celery_mode: Literal["local", "celery"] = Field(default="local")
    # `http://tauri.localhost` is the origin the packaged Windows webview reports; without it
    # the installed desktop client is CORS-blocked while `vite dev` in a browser is not.
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,tauri://localhost,http://tauri.localhost",
    )
    webhook_secret: str = Field(default="dev-webhook-secret")
    default_commission_bp: int = Field(default=1000)
    app_env: Literal["dev", "test", "prod"] = Field(default="dev")
    payment_provider: str = Field(default="mock")
    rate_limit_per_min: int = Field(default=120)

    # SMTP_* unset => email notifications become no-op records (§11).
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from: str = Field(default="no-reply@capacityexchange.test")

    app_name: str = Field(default="capacity-exchange-backend")
    app_version: str = Field(default="1.0.0")

    @property
    def sqlalchemy_database_url(self) -> str:
        """Normalize raw pgserver/compose DSNs to the async psycopg3 dialect."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        elif url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url.strip())


@lru_cache
def get_settings_cached() -> Settings:
    """Convenience for non-request contexts (scripts, job runner)."""
    return Settings()
