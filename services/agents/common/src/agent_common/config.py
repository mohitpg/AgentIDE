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

    # ──────────── LLM ─────────────────────────────────────────────────────────
    openrouter_api_key: str = ""
    # openrouter_model: str = "poolside/laguna-xs.2:free"
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_vision_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3 

    # ── Web Search agent ──────────────────────────────────────────────────────
    serper_api_key: str = ""
    web_search_max_results: int = 5
    web_fetch_chars: int = 4000
    web_fetch_timeout_seconds: float = 15.0

    # ── A2A auth ──────────────────────────────────────────────────────────────
    a2a_auth_token: str = ""

    # ── Agent registry ────────────────────────────────────────────────────────
    # URL the gateway uses to reach this agent, advertised in its Agent Card and
    # registry entry. Empty → http://localhost:<the agent's port> (local dev). Set
    # per agent process (e.g. http://agent-code:8102 in the cluster), never in the
    # shared .env, or every agent would advertise the same address.
    agent_public_url: str = ""
    registry_enabled: bool = True
    gateway_url: str = "http://localhost:8000"
    registry_heartbeat_seconds: int = 30

    # ── MCP ────────────────────────────────────────────────────────────────────
    # Offer the user's desktop MCP tools (via the gateway relay) to tool-using agents.
    mcp_enabled: bool = True


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
