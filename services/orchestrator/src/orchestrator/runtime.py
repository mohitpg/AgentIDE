"""
Orchestrator runtime: owns the compiled graph + LangGraph Postgres checkpointer
and exposes a single streaming entry point used by the API's WebSocket handler.

Lifecycle (driven by the API's FastAPI lifespan):
    orch = Orchestrator(LLMConfig(...), checkpointer_conn_string=...)
    await orch.startup()      # opens the pool, runs checkpointer .setup()
    ... orch.stream_chat(...) # per chat turn
    await orch.shutdown()

Streaming contract: yields `StreamEvent`s from the shared schema so the API and
UI handle orchestrator output the same way they will handle every other agent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk, HumanMessage
from schemas.events import AgentStepEvent, DoneEvent, TokenEvent

from orchestrator.graph import build_graph
from orchestrator.llm import LLMConfig, build_chat_model, system_message

logger = logging.getLogger(__name__)


def _chunk_text(chunk: AIMessageChunk) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    text = ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    return text


class Orchestrator:
    def __init__(self, llm_config: LLMConfig, checkpointer_conn_string: str) -> None:
        self._llm_config = llm_config
        self._conn_string = checkpointer_conn_string
        self._pool = None  # type: ignore[var-annotated]
        self._graph = None  # type: ignore[var-annotated]

    async def startup(self) -> None:
        model = build_chat_model(self._llm_config, streaming=True)
        system = system_message(self._llm_config)
        checkpointer = await self._build_checkpointer()
        self._graph = build_graph(model, system, checkpointer=checkpointer)

    async def _build_checkpointer(self):
        """Postgres checkpointer with a connection pool; falls back to in-memory."""
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            self._pool = AsyncConnectionPool(
                conninfo=self._conn_string,
                open=False,
                max_size=10,
                kwargs={"autocommit": True, "row_factory": dict_row},
            )
            await self._pool.open(wait=True, timeout=5.0)
            saver = AsyncPostgresSaver(self._pool)
            await saver.setup()
            logger.info("LangGraph Postgres checkpointer ready")
            return saver
        except Exception as exc:  # DB down / driver missing — degrade gracefully
            from langgraph.checkpoint.memory import MemorySaver

            logger.warning(
                "Postgres checkpointer unavailable (%s); using in-memory saver. "
                "Thread state will not survive a restart.",
                exc,
            )
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
            return MemorySaver()

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def stream_chat(
        self,
        *,
        content: str,
        run_id: str,
        session_id: str,
        user_id: str,
    ) -> AsyncIterator[object]:
        """
        Run one chat turn. `session_id` is the checkpointer thread, so prior turns
        are restored automatically — we only feed the new user message.

        Yields StreamEvents: agent_step(enter) → token* → agent_step(exit) → done.
        """
        if self._graph is None:
            raise RuntimeError("Orchestrator.startup() was not called")

        started = time.monotonic()
        config = {"configurable": {"thread_id": session_id}}
        graph_input = {
            "messages": [HumanMessage(content=content)],
            "run_id": run_id,
            "session_id": session_id,
            "user_id": user_id,
        }

        yield AgentStepEvent(
            run_id=run_id,
            data={"agent": "assistant", "node": "agent", "phase": "enter"},
        )

        index = 0
        total_tokens = 0
        async for chunk, _meta in self._graph.astream(
            graph_input, config, stream_mode="messages"
        ):
            if not isinstance(chunk, AIMessageChunk):
                continue
            usage = getattr(chunk, "usage_metadata", None)
            if usage:
                total_tokens = usage.get("total_tokens", total_tokens)
            text = _chunk_text(chunk)
            if text:
                yield TokenEvent(run_id=run_id, data={"content": text, "index": index})
                index += 1

        yield AgentStepEvent(
            run_id=run_id,
            data={"agent": "assistant", "node": "agent", "phase": "exit"},
        )
        yield DoneEvent(
            run_id=run_id,
            data={
                "total_tokens": total_tokens,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "agent": "assistant",
            },
        )
