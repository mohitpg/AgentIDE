"""
Code agent.

Its filesystem and git tools are the user's own MCP servers, running in the
desktop app on the user's machine; each request reaches them through the
gateway relay (`agent_common.mcp_tools.RelayToolManager`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from agent_common.base import BaseAgent
from agent_common.config import AgentSettings, get_agent_settings
from agent_common.llm import build_chat_model
from agent_common.mcp_tools import ToolManager, relay_tools_for_request, run_tool_loop

_SKILLS_DIR = Path(__file__).parent / "skills"


class CodeAgent(BaseAgent):
    id = "code"
    name = "Code"
    description = "Generates, edits, and explains code using filesystem + git tools."

    def __init__(
        self,
        *,
        mcp_manager: ToolManager | None = None,
        settings: AgentSettings | None = None,
        **kwargs: object,
    ) -> None:
        settings = settings or get_agent_settings()
        kwargs.setdefault("url", settings.agent_public_url or "http://localhost:8102")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._settings = settings
        # Injected by tests in place of the per-request relay to the desktop.
        self._manager = mcp_manager

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    # ── execution ──────────────────────────────────────────────────────────────
    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        manager = self._manager
        if manager is None and self._settings.mcp_enabled:
            manager = await relay_tools_for_request(self.id, self._settings)
        # No tools to offer (MCP disabled, or no request scope) → plain LLM
        # streaming from the BaseAgent default.
        if manager is None:
            async for chunk in super().run(skill_id, user_input, run_id=run_id):
                yield chunk
            return

        skill = self.get_skill(skill_id) or self._skills[self.default_skill_id()]
        model = build_chat_model(self._llm_config, streaming=True)
        async for chunk in run_tool_loop(
            model=model,
            manager=manager,
            agent_id=self.id,
            system_prompt=skill.prompt,
            user_input=user_input,
        ):
            yield chunk
