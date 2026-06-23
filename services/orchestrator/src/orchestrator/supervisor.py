"""
Recurrent supervisor graph — assembly only.

    START → supervisor ─┬─ delegate(next agent) → appends output → supervisor  ⟲
                        │
                        └─ respond → END   (compose & stream the final answer)

The supervisor is called repeatedly: each pass it reads the request plus
everything specialists have produced so far and emits `{next: <agent>, skill}` to
make more progress, or `{next: "respond"}` to finish. `delegate` runs one
specialist over A2A and loops back; `respond` composes the single final answer
and is also the fast path for trivial turns. The loop is bounded by `STEP_CAP`.

The three nodes live in their own modules under `orchestrator.nodes`; this module
just wires them into the graph. `STEP_CAP`, `RESPOND`, `SUPERVISOR_PROMPT`,
`_parse_decision`, and `A2AStreamClient` are re-exported here for backward
compatibility with existing importers.

Streaming: nodes emit `StreamEvent`s via LangGraph's stream writer (stream_mode
="custom"). Under `ainvoke` (tests) the writer is a no-op, so state is still
built correctly.
"""

from __future__ import annotations

import uuid

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator.directory import AgentDirectory
from orchestrator.nodes import DelegateNode, RespondNode, SupervisorNode
from orchestrator.nodes.delegate import A2AStreamClient
from orchestrator.nodes.supervisor import (
    RESPOND,
    STEP_CAP,
    SUPERVISOR_PROMPT,
    _parse_decision,
)
from orchestrator.state import GraphState

__all__ = [
    "A2AStreamClient",
    "RESPOND",
    "STEP_CAP",
    "SUPERVISOR_PROMPT",
    "_parse_decision",
    "build_supervisor_graph",
    "new_run_id",
]


def build_supervisor_graph(
    router_model: BaseChatModel,
    general_model: BaseChatModel,
    general_system: SystemMessage,
    client: A2AStreamClient,
    directory: AgentDirectory,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    supervisor = SupervisorNode(router_model, directory)
    delegate = DelegateNode(client, directory)
    respond = RespondNode(general_model, general_system)

    graph = StateGraph(GraphState)
    # Register the bound async `__call__` methods (not the instances) so LangGraph
    # detects each node as a coroutine and awaits it on the async path.
    graph.add_node("supervisor", supervisor.__call__)
    graph.add_node("delegate", delegate.__call__)
    graph.add_node("respond", respond.__call__)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", supervisor.pick, {"delegate": "delegate", "respond": "respond"}
    )
    graph.add_edge("delegate", "supervisor")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)


def new_run_id() -> str:
    return str(uuid.uuid4())
