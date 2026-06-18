"""
Phase 2 supervisor graph.

    START → route → ├─ delegate → END   (call a specialist A2A agent)
                    └─ general  → END   (answer directly — fallback)

`route` asks an LLM to classify the user's intent into one of the known agents
(code, summarizer) or `general`. `delegate` maps the turn into the chosen skill's
input, streams the specialist's tokens back through LangGraph's custom stream so
the orchestrator/UI render them with the same unified event schema, and records
an artifact. `general` answers inline like the Phase-1 graph.

Streaming: nodes emit `StreamEvent`s via LangGraph's stream writer (stream_mode
="custom"). Under `ainvoke` (tests) the writer is a no-op, so state is still
built correctly.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from schemas.events import AgentStepEvent, DoneEvent, ErrorEvent, TokenEvent

from orchestrator.directory import AgentDirectory
from orchestrator.state import GraphState

logger = logging.getLogger(__name__)

try:  # available in langgraph >= 0.2
    from langgraph.config import get_stream_writer
except ImportError:  # pragma: no cover - very old langgraph
    def get_stream_writer():  # type: ignore[misc]
        return lambda _event: None


def _writer():
    """Stream writer that degrades to a no-op under ainvoke / non-custom streaming."""
    try:
        return get_stream_writer()
    except Exception:
        return lambda _event: None


ROUTER_PROMPT = (
    "You are the supervisor router for AgentIDE. Classify the user's latest "
    "message and choose exactly one agent and skill to handle it.\n\n"
    "Agents and skills:\n"
    "- code: generate (write new code from a spec), edit (modify given code), "
    "explain (explain a snippet)\n"
    "- summarizer: summarize-file (condense file contents), summarize-thread "
    "(condense a conversation), tl;dr (gist of arbitrary text)\n"
    "- rag: answer (answer a question using the indexed documents / knowledge "
    "base, with citations), retrieve (return the relevant source passages). Use "
    "rag when the user asks about uploaded/indexed documents, the docs, or the "
    "knowledge base.\n"
    "- general: for anything conversational that needs no specialist\n\n"
    'Respond with ONLY a JSON object: {"agent": "...", "skill": "...", '
    '"reason": "..."}. Use "general" with "skill": null when no specialist fits.'
)

# Per-skill adapters: build the skill's input payload from the turn text + history.
_VALID = {
    "code": {"generate", "edit", "explain"},
    "summarizer": {"summarize-file", "summarize-thread", "tl;dr"},
    "rag": {"answer", "retrieve"},
}


class A2AStreamClient(Protocol):
    """Just the slice of A2AClient the supervisor needs (duck-typed for tests)."""

    def stream(
        self, base_url: str, skill_id: str, input_data: dict[str, Any], run_id: str | None = ...
    ): ...


def _last_human_text(state: GraphState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _history(state: GraphState) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in state["messages"]:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        if isinstance(msg.content, str):
            out.append({"role": role, "content": msg.content})
    return out


def _build_skill_input(
    agent: str, skill: str | None, text: str, history: list[dict[str, str]]
) -> dict[str, Any]:
    # Skills are defined as SKILL.md prompts that interpret the raw turn text, so
    # the supervisor just forwards it. The chosen skill is sent as A2A metadata.
    return {"text": text}


def _parse_decision(raw: str) -> dict[str, Any]:
    """Extract the router's JSON object, defaulting to `general` on any failure."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            agent = data.get("agent", "general")
            skill = data.get("skill")
            if agent in _VALID and skill not in _VALID[agent]:
                skill = next(iter(_VALID[agent]))  # snap to a valid skill
            if agent not in _VALID:
                agent, skill = "general", None
            return {"agent": agent, "skill": skill, "reason": data.get("reason", "")}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("router returned unparseable decision: %r", raw)
    return {"agent": "general", "skill": None, "reason": "router fallback"}


def build_supervisor_graph(
    router_model: BaseChatModel,
    general_model: BaseChatModel,
    general_system: SystemMessage,
    client: A2AStreamClient,
    directory: AgentDirectory,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    # ── route ─────────────────────────────────────────────────────────────────
    async def route_node(state: GraphState) -> dict:
        writer = _writer()
        run_id = state.get("run_id", "")
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "supervisor", "node": "route", "phase": "enter"}))

        text = _last_human_text(state)
        response = await router_model.ainvoke(
            [SystemMessage(content=ROUTER_PROMPT), HumanMessage(content=text)]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        decision = _parse_decision(raw)
        decision["input"] = _build_skill_input(
            decision["agent"], decision["skill"], text, _history(state)
        )

        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "supervisor", "node": "route", "phase": "exit",
            "decision": {"agent": decision["agent"], "skill": decision["skill"],
                         "reason": decision["reason"]}}))
        return {"active_agent": decision["agent"], "route_decision": decision}

    def pick(state: GraphState) -> str:
        agent = (state.get("route_decision") or {}).get("agent")
        return "delegate" if agent in directory.known_agents() else "general"

    # ── delegate to a specialist A2A agent ─────────────────────────────────────
    async def delegate_node(state: GraphState) -> dict:
        writer = _writer()
        run_id = state.get("run_id", "")
        decision = state["route_decision"] or {}
        agent_id, skill, payload = decision["agent"], decision["skill"], decision["input"]
        base_url = directory.url_for(agent_id) or ""

        writer(AgentStepEvent(run_id=run_id, data={
            "agent": agent_id, "node": "delegate", "phase": "enter", "skill": skill}))

        text, index, result = "", 0, {}
        try:
            async for ev in client.stream(base_url, skill, payload, run_id):
                if ev.get("type") == "token":
                    piece = ev["data"].get("content", "")
                    text += piece
                    writer(TokenEvent(run_id=run_id, data={"content": piece, "index": index}))
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

        artifact = {"agent": agent_id, "skill": skill, "result": result or {"text": text}}
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": agent_id, "node": "delegate", "phase": "exit"}))
        writer(DoneEvent(run_id=run_id, data={"agent": agent_id}))
        return {"messages": [AIMessage(content=text)], "artifacts": [artifact],
                "active_agent": agent_id}

    # ── general fallback (answer inline) ───────────────────────────────────────
    async def general_node(state: GraphState) -> dict:
        writer = _writer()
        run_id = state.get("run_id", "")
        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "assistant", "node": "general", "phase": "enter"}))

        text, index = "", 0
        async for chunk in general_model.astream([general_system, *state["messages"]]):
            if not isinstance(chunk, AIMessageChunk):
                continue
            piece = chunk.content if isinstance(chunk.content, str) else ""
            if piece:
                text += piece
                writer(TokenEvent(run_id=run_id, data={"content": piece, "index": index}))
                index += 1

        writer(AgentStepEvent(run_id=run_id, data={
            "agent": "assistant", "node": "general", "phase": "exit"}))
        writer(DoneEvent(run_id=run_id, data={"agent": "assistant"}))
        return {"messages": [AIMessage(content=text)], "active_agent": "assistant"}

    graph = StateGraph(GraphState)
    graph.add_node("route", route_node)
    graph.add_node("delegate", delegate_node)
    graph.add_node("general", general_node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", pick, {"delegate": "delegate", "general": "general"})
    graph.add_edge("delegate", END)
    graph.add_edge("general", END)
    return graph.compile(checkpointer=checkpointer)


def new_run_id() -> str:
    return str(uuid.uuid4())
