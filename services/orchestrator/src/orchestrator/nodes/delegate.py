"""
The delegate node — runs one specialist agent over A2A, then loops back.

Streams the specialist's tokens through the unified event schema, appends its
output to `messages` as an `AIMessage` tagged with the agent `name` + `skill` (the
single source of truth the next supervisor pass, downstream delegates, and respond
all read), and increments the loop counter. Best-effort: a down/failed agent
degrades to a note instead of aborting the turn, so the supervisor can pick another
agent or finish. It emits only the `agent_step` exit — the terminal `respond` node
owns the single `DoneEvent` for the whole turn.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from langchain_core.messages import AIMessage
from schemas.events import AgentStepEvent, Channel, ErrorEvent, TokenEvent

from orchestrator.directory import AgentDirectory
from orchestrator.nodes.base import stream_writer
from orchestrator.state import GraphState

logger = logging.getLogger(__name__)


class A2AStreamClient(Protocol):
    """Just the slice of A2AClient the delegate needs (duck-typed for tests)."""

    def stream(
        self, base_url: str, skill_id: str, input_data: dict[str, Any], run_id: str | None = ...
    ): ...


class DelegateNode:
    """Delegates the supervisor's chosen skill to a specialist A2A agent."""

    def __init__(self, client: A2AStreamClient, directory: AgentDirectory) -> None:
        self._client = client
        self._directory = directory

    async def __call__(self, state: GraphState) -> dict:
        writer = stream_writer()
        run_id = state.get("run_id", "")
        decision = state["route_decision"] or {}
        agent_id, skill, payload = decision["next"], decision["skill"], decision["input"]
        base_url = self._directory.url_for(agent_id) or ""

        writer(AgentStepEvent(run_id=run_id, data={
            "agent": agent_id, "node": "delegate", "phase": "enter", "skill": skill}))

        text, index, result = "", 0, {}
        try:
            async for ev in self._client.stream(base_url, skill, payload, run_id):
                if ev.get("type") == "token":
                    piece = ev["data"].get("content", "")
                    text += piece
                    # Intermediate step output → the UI's collapsible "Thinking" stream.
                    writer(TokenEvent(run_id=run_id, data={
                        "content": piece, "index": index, "channel": Channel.THINKING}))
                    index += 1
                elif ev.get("type") == "done":
                    result = ev.get("data", {})
            if not text:  # non-streaming agent: surface the result body
                text = result.get("summary") or result.get("output") or result.get(
                    "explanation", "")
        except Exception as exc:  # agent down / network error — degrade gracefully
            logger.exception("delegate to %s failed", agent_id)
            writer(ErrorEvent(run_id=run_id, data={
                "code": "a2a_unavailable", "message": str(exc), "recoverable": True}))
            text = f"The {agent_id} agent is currently unavailable."

        # Exit only — no DoneEvent here; the terminal `respond` node emits the
        # single DoneEvent once the whole turn is composed.
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": agent_id, "node": "delegate", "phase": "exit"}))
        return {
            # The `add_messages` reducer appends this to the running history, so the
            # next supervisor pass, any downstream delegate, and respond all see it.
            # `name`/`skill` tag it as this agent's output (replacing the old
            # artifacts buffer); `split_turn` reads these back.
            "messages": [AIMessage(content=text, name=agent_id,
                                   additional_kwargs={"skill": skill})],
            "active_agent": agent_id,
            "step_count": state.get("step_count", 0) + 1,
        }
