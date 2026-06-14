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
    database_url: str = "postgresql+asyncpg://agentide:agentide@localhost:5433/agentide"
    # Sync URL used by Alembic migrations only
    database_url_sync: str = "postgresql+psycopg2://agentide:agentide@localhost:5433/agentide"
    # Driverless URL for the LangGraph Postgres checkpointer (psycopg3)
    database_url_psycopg: str = "postgresql://agentide:agentide@localhost:5433/agentide"

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Agent registry (Phase 2) ──────────────────────────────────────────────
    # An agent is "live" if it has heartbeat within this window. Set to ~3x the
    # agent heartbeat interval so a couple of missed beats don't drop it.
    registry_ttl_seconds: int = 90
    # How long the live-agent list is cached in Redis before re-reading Postgres.
    registry_cache_ttl_seconds: int = 30
    # Shared bearer the supervisor sends when delegating to agents; must match the
    # agents' A2A_AUTH_TOKEN. Empty = no auth header (dev default).
    a2a_auth_token: str = ""

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
    openrouter_api_key: str = "" # test key
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.7

    # ── Observability ─────────────────────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
