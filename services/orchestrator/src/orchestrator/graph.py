"""
START → agent → END

The `agent` node calls the chat model with the system prompt prepended to the
conversation history (which the checkpointer accumulates per thread via the
`add_messages` reducer on GraphState). Later phases replace this node with a
supervisor that routes to specialist A2A agents.
"""

from __future__ import annotations

from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator.state import GraphState


def build_graph(
    model: ChatOpenRouter,
    system: SystemMessage,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    async def agent_node(state: GraphState) -> dict:
        response = await model.ainvoke([system, *state["messages"]])
        return {"messages": [response], "active_agent": "assistant"}

    graph = StateGraph(GraphState)
    graph.add_node("agent", agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    return graph.compile(checkpointer=checkpointer)
