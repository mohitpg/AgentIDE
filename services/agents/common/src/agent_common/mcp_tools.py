"""
Agent-side MCP wiring: load assigned servers, and run a tool-calling loop.

Specialist agents are MCP *clients*. On startup an agent asks the gateway which
MCP servers it's assigned (``GET /mcp/agent/{agent_id}``), builds an `MCPManager`
over those configs, and connects them. At run time `run_tool_loop` binds the
exposed tools onto the LLM and drives the classic call→execute→observe loop until
the model produces a final answer.

Both helpers degrade gracefully: no gateway / no servers / no tools ⇒ the agent
just answers from the LLM. This keeps tool use opt-in and never fatal.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from mcp_manager import MCPManager, MCPServerConfig, builtin_servers

logger = logging.getLogger(__name__)

# Hard cap on tool iterations so a confused model can't loop forever.
MAX_TOOL_ITERS = 6


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


async def fetch_agent_mcp_configs(
    gateway_url: str, agent_id: str, *, auth_token: str = "", timeout: float = 10.0
) -> list[MCPServerConfig]:
    """Fetch the MCP servers assigned to ``agent_id`` from the gateway.

    Returns ``[]`` (rather than raising) if the gateway is unreachable or has no
    assignments — the caller decides whether to fall back to built-ins.
    """
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    url = f"{gateway_url.rstrip('/')}/mcp/agent/{agent_id}"
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return [MCPServerConfig.model_validate(item) for item in resp.json()]
    except Exception as exc:
        logger.warning("could not fetch MCP config for %s from gateway: %s", agent_id, exc)
        return []


async def build_agent_manager(
    *,
    agent_id: str,
    gateway_url: str,
    auth_token: str = "",
    fallback_workspace: str | None = None,
) -> MCPManager:
    """Build (but don't connect) an `MCPManager` for an agent.

    Prefers the gateway's assigned servers; if there are none and a
    ``fallback_workspace`` is given, falls back to the built-in core set scoped
    to that directory so the agent is useful even before anything is registered.
    """
    configs = await fetch_agent_mcp_configs(gateway_url, agent_id, auth_token=auth_token)
    if not configs and fallback_workspace:
        logger.info("no MCP assignments for %s; using built-in servers", agent_id)
        configs = builtin_servers(fallback_workspace)
    return MCPManager(configs)


async def run_tool_loop(
    *,
    model: BaseChatModel,
    manager: MCPManager,
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


async def _safe_call(manager: MCPManager, call: dict) -> str:
    name = call.get("name", "")
    args = call.get("args", {}) or {}
    try:
        return await manager.call(name, args)
    except Exception as exc:  # surface to the model so it can recover
        logger.warning("MCP tool '%s' failed: %s", name, exc)
        return f"Error calling {name}: {exc}"
