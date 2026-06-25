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
    # openrouter_model: str = "poolside/laguna-xs.2:free"
    openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    # Multimodal model for the Vision agent (Phase 5). Must be vision-capable; the
    # text default above generally is not. Override via OPENROUTER_VISION_MODEL.
    openrouter_vision_model: str = "nvidia/nemotron-3.5-content-safety:free"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3  # specialists run cooler than the chat default

    # ── A2A auth ──────────────────────────────────────────────────────────────
    # Shared bearer secret enforced on skill invocation. Empty = auth disabled
    # (dev default); set A2A_AUTH_TOKEN to require `Authorization: Bearer <token>`.
    a2a_auth_token: str = ""

    # ── Agent registry (Phase 2) ──────────────────────────────────────────────
    # On startup an agent registers its Agent Card with the gateway and heartbeats
    # so the supervisor can discover it. Disabled by default so an agent run in
    # isolation doesn't depend on the gateway; the stack enables it via env.
    registry_enabled: bool = True
    gateway_url: str = "http://localhost:8000"
    registry_heartbeat_seconds: int = 30

    # ── MCP (Phase 6) ─────────────────────────────────────────────────────────
    # Tool-using agents (Code, Web) connect MCP servers on startup. They fetch
    # their assignments from the gateway's /mcp API; if there are none they fall
    # back to the built-in core set scoped to `mcp_workspace_dir`. Set
    # MCP_ENABLED=false to disable tool use entirely (pure-LLM agents).
    mcp_enabled: bool = True
    # Sandbox root for the built-in filesystem/git servers. Empty → process CWD.
    mcp_workspace_dir: str = ""


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
