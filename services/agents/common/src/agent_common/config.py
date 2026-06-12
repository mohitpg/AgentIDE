"""
Agent settings, loaded from environment / repo-root .env.

Every A2A agent process reads the same OpenRouter + auth config so they behave
consistently whether launched individually or via docker-compose. Mirrors the
env var names used by `api.config.Settings` so a single .env drives the stack.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → agent_common → src → common → agents → services → <repo root>
_ENV_FILE = Path(__file__).resolve().parents[5] / ".env"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM (OpenRouter — the stack-wide default chosen in Phase 1) ────────────
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3  # specialists run cooler than the chat default

    # ── A2A auth ──────────────────────────────────────────────────────────────
    # Shared bearer secret enforced on skill invocation. Empty = auth disabled
    # (dev default); set A2A_AUTH_TOKEN to require `Authorization: Bearer <token>`.
    a2a_auth_token: str = ""


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
