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
from mcp_manager import MCPManager, builtin_servers

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
        settings = settings or get_agent_settings()
        kwargs.setdefault("url", settings.agent_public_url or "http://localhost:8102")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._settings = settings
        # An injected manager (tests) is owned by the caller; one we build here is
        # ours to connect and tear down.
        self._manager = mcp_manager
        self._owns_manager = mcp_manager is None
        # Directory the filesystem/git tools are sandboxed to. Switchable at
        # runtime via `set_workspace` (the gateway calls it when the user picks a
        # new folder in the UI).
        self._workspace = self._settings.mcp_workspace_dir or os.getcwd()

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    # ── MCP lifecycle ──────────────────────────────────────────────────────────
    async def astartup(self) -> None:
        if not self._settings.mcp_enabled:
            return
        if self._manager is None:
            self._manager = await build_agent_manager(
                agent_id=self.id,
                gateway_url=self._settings.gateway_url,
                auth_token=self._settings.a2a_auth_token,
                fallback_workspace=self._workspace,
            )
        await self._manager.connect()

    async def ashutdown(self) -> None:
        if self._owns_manager and self._manager is not None:
            await self._manager.aclose()
            self._manager = None

    async def set_workspace(self, workspace: str) -> None:
        """Re-root the filesystem/git tools at ``workspace`` without a restart.

        Rebuilds the built-in MCP servers scoped to the new directory and swaps
        them in atomically: connect the replacement first, then tear down the old
        one, so a bad path leaves the agent on its current (working) tools.
        """
        self._workspace = workspace
        if not self._settings.mcp_enabled:
            return
        new_manager = MCPManager(builtin_servers(workspace))
        await new_manager.connect()
        old = self._manager
        self._manager = new_manager
        self._owns_manager = True
        if old is not None:
            await old.aclose()

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
