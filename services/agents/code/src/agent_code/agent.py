"""
Code agent.

"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from agent_common.base import BaseAgent
from agent_common.config import AgentSettings, get_agent_settings
from agent_common.llm import build_chat_model
from agent_common.mcp_tools import build_agent_manager, run_tool_loop
from mcp_manager import MCPManager

_SKILLS_DIR = Path(__file__).parent / "skills"


class CodeAgent(BaseAgent):
    id = "code"
    name = "Code"
    description = "Generates, edits, and explains code using filesystem + git tools."

    def __init__(
        self,
        *,
        mcp_manager: MCPManager | None = None,
        settings: AgentSettings | None = None,
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("url", "http://localhost:8102")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._settings = settings or get_agent_settings()
        # An injected manager (tests) is owned by the caller; one we build here is
        # ours to connect and tear down.
        self._manager = mcp_manager
        self._owns_manager = mcp_manager is None

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    # ── MCP lifecycle ──────────────────────────────────────────────────────────
    async def astartup(self) -> None:
        if not self._settings.mcp_enabled:
            return
        if self._manager is None:
            workspace = self._settings.mcp_workspace_dir or os.getcwd()
            self._manager = await build_agent_manager(
                agent_id=self.id,
                gateway_url=self._settings.gateway_url,
                auth_token=self._settings.a2a_auth_token,
                fallback_workspace=workspace,
            )
        await self._manager.connect()

    async def ashutdown(self) -> None:
        if self._owns_manager and self._manager is not None:
            await self._manager.aclose()
            self._manager = None

    # ── execution ──────────────────────────────────────────────────────────────
    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        # No connected manager (MCP disabled, or invoked outside the app lifespan)
        # → plain LLM streaming from the BaseAgent default.
        print("codeagent->",self._manager)
        if self._manager is None:
            async for chunk in super().run(skill_id, user_input, run_id=run_id):
                yield chunk
            return

        skill = self.get_skill(skill_id) or self._skills[self.default_skill_id()]
        model = build_chat_model(self._llm_config, streaming=True)
        async for chunk in run_tool_loop(
            model=model,
            manager=self._manager,
            agent_id=self.id,
            system_prompt=skill.prompt,
            user_input=user_input,
        ):
            yield chunk
