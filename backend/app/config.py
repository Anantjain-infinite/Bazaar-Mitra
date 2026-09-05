"""Centralized application settings.

All configuration is read from environment variables (via `.env.local` in
development, or real environment variables in deployment). Nothing in this
module should ever hard-code a secret — see `.env.example` at the backend
root for the full list of variables this app understands.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_env: str = "development"
    log_level: str = "INFO"
    agent_name: str = "Bazaar Mitra"
    cors_origins: str = "http://localhost:3000"

    # --- Database ---
    # Async URL used by the running app (asyncpg driver).
    database_url: str = (
        "postgresql+asyncpg://postgres:Anant123098@localhost:5432/bazaar_mitra"
    )
    # Sync URL used by Alembic (psycopg2 driver). Derived automatically if not set.
    database_url_sync: str | None = None

    # --- Razorpay (Test Mode) ---
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # --- Policy defaults (used when a merchant has no explicit policy row) ---
    max_transaction_amount: int = 5000
    daily_transaction_limit: int = 10000
    default_currency: str = "INR"

    # --- LiveKit / voice (already used by backend/src, duplicated here so
    # the FastAPI app can issue LiveKit tokens for the voice widget later) ---
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    @property
    def sync_database_url(self) -> str:
        """Return a psycopg2-style URL for Alembic, derived from database_url."""
        if self.database_url_sync:
            return self.database_url_sync
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — import and call this, don't instantiate Settings() directly."""
    return Settings()
