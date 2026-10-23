"""
Agent-side MCP wiring: the user's desktop tools, and a tool-calling loop.

MCP servers run on the user's own machine, inside the desktop app. An agent
reaches them through the gateway's relay: `RelayToolManager` fetches the tools
the user's desktop exposes to this agent (``GET /mcp/relay/tools``) and sends
each call to ``POST /mcp/relay/call``, on behalf of the user in the request scope
(`agent_common.scope`). At run time `run_tool_loop` binds those tools onto the
LLM and drives the classic call→execute→observe loop until the model produces a
final answer.

Everything degrades gracefully: no request scope / desktop offline / no tools ⇒
the agent just answers from the LLM, and a failed call (declined, timed out,
desktop gone) is fed back to the model as an error string.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from mcp_manager import ExposedTool, MCPToolError, MCPToolInfo, to_openai_tool
from opentelemetry import trace

from agent_common.config import AgentSettings
from agent_common.scope import current_scope

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

# Hard cap on tool iterations so a confused model can't loop forever.
MAX_TOOL_ITERS = 6

# A relayed call can wait on the user confirming it on the desktop; the gateway
# enforces the real deadline (MCP_RELAY_TIMEOUT_SECONDS), this is a backstop.
_CALL_TIMEOUT = httpx.Timeout(10.0, read=600.0)


class ToolManager(Protocol):
    """What `run_tool_loop` needs: `MCPManager` and `RelayToolManager` both fit."""

    def openai_tools_for_agent(self, agent_id: str) -> list[dict[str, Any]]: ...
    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str: ...


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


class RelayToolManager:
    """One user's desktop MCP tools for one agent, reached through the gateway relay.

    Build it per request and `load` it before use. The gateway has already
    filtered the tools by the desktop's exposure rules for this agent.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        auth_token: str,
        user_id: uuid.UUID,
        agent_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._gateway = gateway_url.rstrip("/")
        self._auth_token = auth_token
        self._user_id = user_id
        self._agent_id = agent_id
        self._transport = transport  # injectable for tests
        self._tools: list[ExposedTool] = []

    def _client(self, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
        headers = {"X-On-Behalf-Of": str(self._user_id)}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return httpx.AsyncClient(
            base_url=self._gateway, timeout=timeout, headers=headers, transport=self._transport,
        )

    async def load(self) -> RelayToolManager:
        """Fetch the tools; none if the gateway is unreachable or the desktop offline."""
        try:
            async with self._client(timeout=10.0) as http:
                resp = await http.get("/mcp/relay/tools", params={"agent_id": self._agent_id})
                resp.raise_for_status()
                self._tools = [
                    ExposedTool(
                        item["name"],
                        item["server_id"],
                        MCPToolInfo(item["tool"], item.get("description") or "",
                                    item.get("input_schema") or {}),
                    )
                    for item in resp.json()
                ]
        except Exception as exc:
            logger.warning("could not load desktop tools for %s: %s", self._agent_id, exc)
            self._tools = []
        return self

    def openai_tools_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        return [to_openai_tool(et) for et in self._tools]

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Run a tool on the user's desktop; raises with the reason if it didn't run."""
        # GenAI semconv tool span; arguments and results are not recorded.
        with _tracer.start_as_current_span(f"execute_tool {name}", attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "agentide.mcp.relay": True,
        }):
            async with self._client(timeout=_CALL_TIMEOUT) as http:
                resp = await http.post("/mcp/relay/call", json={
                    "agent_id": self._agent_id, "name": name, "arguments": arguments or {},
                })
                resp.raise_for_status()
                body = resp.json()
        if not body.get("ok"):
            raise MCPToolError(body.get("error") or f"tool '{name}' failed")
        return str(body.get("content") or "")


async def relay_tools_for_request(
    agent_id: str, settings: AgentSettings
) -> RelayToolManager | None:
    """The current request's user's desktop tools, or None outside a request scope."""
    scope = current_scope()
    if scope is None:
        return None
    manager = RelayToolManager(
        gateway_url=settings.gateway_url,
        auth_token=settings.a2a_auth_token,
        user_id=scope.user_id,
        agent_id=agent_id,
    )
    return await manager.load()


async def run_tool_loop(
    *,
    model: BaseChatModel,
    manager: ToolManager,
    agent_id: str,
    system_prompt: str,
    user_input: str,
    max_iters: int = MAX_TOOL_ITERS,
) -> AsyncIterator[str]:
    """Stream an answer, letting the model call this agent's MCP tools.

    No exposed tools ⇒ a plain streamed LLM answer. Otherwise: invoke (with tools
    bound) → run any tool calls via the manager → feed results back → repeat,
    streaming the final tool-free turn token by token.
    """
    tools = manager.openai_tools_for_agent(agent_id)
    messages: list = [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]

    if not tools:
        async for piece in _astream(model, messages):
            yield piece
        return

    bound = model.bind_tools(tools)
    for _ in range(max_iters):
        ai = await bound.ainvoke(messages)
        messages.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            # Model answered without (further) tools. Re-stream this turn so the
            # UI gets tokens rather than one block; fall back to the text we have.
            text = _text(ai.content)
            if text:
                yield text
            return
        for call in calls:
            result = await _safe_call(manager, call)
            messages.append(ToolMessage(content=result, tool_call_id=call.get("id", "")))

    # Iteration budget spent — ask once more without tools for a final answer.
    async for piece in _astream(model, messages):
        yield piece


async def _astream(model: BaseChatModel, messages: list) -> AsyncIterator[str]:
    async for chunk in model.astream(messages):
        if isinstance(chunk, AIMessageChunk):
            piece = _text(chunk.content)
            if piece:
                yield piece


async def _safe_call(manager: ToolManager, call: dict) -> str:
    name = call.get("name", "")
    args = call.get("args", {}) or {}
    try:
        return await manager.call(name, args)
    except Exception as exc:  # surface to the model so it can recover
        logger.warning("MCP tool '%s' failed: %s", name, exc)
        return f"Error calling {name}: {exc}"
