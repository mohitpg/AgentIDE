"""
Application settings loaded from environment variables / .env file.
All services that need config import `get_settings()` from here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env, resolved absolutely so it loads no matter which directory
# a process starts from (alembic runs from apps/api, workers from services/...).
# config.py → src/api/ → apps/api/ → apps/ → <repo root>
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_name: str = "AgentIDE API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    debug: bool = False

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://agentide:agentide@localhost:5432/agentide"
    # Sync URL used by Alembic migrations only
    database_url_sync: str = "postgresql+psycopg2://agentide:agentide@localhost:5432/agentide"

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── RabbitMQ ─────────────────────────────────────────────────────────────
    rabbitmq_url: str = "amqp://agentide:agentide@localhost:5672/"

    # ── Auth (JWT) ────────────────────────────────────────────────────────────
    secret_key: str = "CHANGE_ME_IN_PRODUCTION"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── LLM ──────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ── Observability ─────────────────────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
