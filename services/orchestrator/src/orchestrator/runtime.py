"""
Orchestrator runtime: owns the compiled **supervisor graph** + LangGraph Postgres
checkpointer and exposes a single streaming entry point used by the API's
WebSocket handler.

The graph is the supervisor (`build_supervisor_graph`) — it routes each
turn to a specialist A2A agent or answers inline. The supervisor's nodes emit the
unified `StreamEvent`s directly via LangGraph's custom stream writer, so
`stream_chat` simply forwards them; the API/UI render every agent's activity the
same way.

Discovery is dynamic: before each turn the directory (agent_id → A2A URL) is
refreshed from the agent registry via an injected `directory_provider`, so the
supervisor only delegates to agents that are currently registered and live. The
A2A client itself lives in `agent_common`; the orchestrator constructs it at
startup (lazily imported so the package stays importable without the agent stack).

Lifecycle (driven by the API's FastAPI lifespan):
    orch = Orchestrator(LLMConfig(...), conn, directory_provider=...)
    await orch.startup()      # opens the pool, builds the supervisor graph
    ... orch.stream_chat(...) # per chat turn
    await orch.shutdown()
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from langchain_core.messages import HumanMessage
from schemas.events import DoneEvent

from orchestrator.directory import AgentDirectory
from orchestrator.llm import LLMConfig, build_chat_model, system_message
from orchestrator.supervisor import STEP_CAP, build_supervisor_graph

logger = logging.getLogger(__name__)

# Returns the current {agent_id: A2A base URL} map (e.g. from the registry).
DirectoryProvider = Callable[[], Awaitable[dict[str, str]]]


class Orchestrator:
    def __init__(
        self,
        llm_config: LLMConfig,
        checkpointer_conn_string: str,
        *,
        a2a_auth_token: str = "",
        directory_provider: DirectoryProvider | None = None,
    ) -> None:
        self._llm_config = llm_config
        self._conn_string = checkpointer_conn_string
        self._auth_token = a2a_auth_token
        self._directory_provider = directory_provider
        # Shared, mutable directory the supervisor reads on every run; refreshed
        # in place from the provider before each turn (the graph closes over it).
        self._directory = AgentDirectory.from_env()
        self._pool = None  # type: ignore[var-annotated]
        self._graph = None  # type: ignore[var-annotated]
        self._checkpointer = None  # type: ignore[var-annotated]

    async def startup(self) -> None:
        # Lazy import: keeps `orchestrator` importable (and unit-testable with an
        # injected graph) without the full agent stack installed.
        from agent_common.client import A2AAgentClient

        router_model = build_chat_model(self._llm_config, streaming=False)
        general_model = build_chat_model(self._llm_config, streaming=True)
        general_system = system_message(self._llm_config)
        client = A2AAgentClient(auth_token=self._auth_token)
        checkpointer = await self._build_checkpointer()
        self._checkpointer = checkpointer
        self._graph = build_supervisor_graph(
            router_model=router_model,
            general_model=general_model,
            general_system=general_system,
            client=client,
            directory=self._directory,
            checkpointer=checkpointer,
        )

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

    async def delete_threads(self, thread_ids: list[str]) -> None:
        """Drop the checkpointed state of deleted conversations (best-effort).

        Checkpoints are keyed by session id and would otherwise outlive the
        conversation: deleted chat content would linger, and a later session
        reusing that id would have the old history restored into it.
        """
        if self._checkpointer is None:
            return
        for thread_id in thread_ids:
            try:
                await self._checkpointer.adelete_thread(thread_id)
            except Exception as exc:  # DB blip — the row delete already happened
                logger.warning("failed to delete checkpoint thread %s: %s", thread_id, exc)

    async def _refresh_directory(self) -> None:
        """Pull the live agent set from the registry into the shared directory."""
        if self._directory_provider is None:
            return
        try:
            urls = await self._directory_provider()
        except Exception as exc:  # registry blip — keep last-known agents
            logger.warning(
                "agent directory refresh failed (%s); using last-known agents", exc
            )
            return
        self._directory.urls.clear()
        self._directory.urls.update(urls)

    async def stream_chat(
        self,
        *,
        content: str,
        run_id: str,
        session_id: str,
        user_id: str,
        project_id: str = "",
        mode: str = "auto",
    ) -> AsyncIterator[object]:
        """
        Run one chat turn through the supervisor loop. `session_id` is the
        checkpointer thread, so prior turns are restored automatically (via the
        `messages` channel's `add_messages` reducer) — we only feed the new user
        message, which the reducer appends to that restored history. `mode`
        ("auto"|"agent"|"chat") and `step_count` are per-turn scalars with no
        reducer, so the values seeded here overwrite whatever the checkpoint
        restored, scoping them to this turn. `user_id` / `project_id` are the
        caller's row scope, handed to every specialist the turn delegates to.

        Forwards the supervisor's StreamEvents (agent_step / token / done / error)
        as they are emitted, stamping `duration_ms` onto the terminal `done`.
        """
        if self._graph is None:
            raise RuntimeError("Orchestrator.startup() was not called")

        await self._refresh_directory()

        started = time.monotonic()
        config = {
            "configurable": {"thread_id": session_id},
            # Hard backstop in case the step_count cap is ever bypassed: each loop
            # is at most supervisor+delegate (2 supersteps), plus a final
            # supervisor→respond pair (~2*cap+2). A little headroom is added so this
            # backstop never preempts the intended STEP_CAP bound on a normal run.
            "recursion_limit": 2 * STEP_CAP + 4,
        }
        graph_input = {
            # add_messages appends this to the checkpoint-restored history, so the
            # whole conversation stays in one place (the single source of truth).
            "messages": [HumanMessage(content=content)],
            "run_id": run_id,
            "session_id": session_id,
            "user_id": user_id,
            "project_id": project_id,
            # Per-turn loop scalars (no reducer ⇒ these overwrite the restored
            # checkpoint values, scoping them to this turn). Specialist outputs are
            # no longer tracked in a separate buffer — they live in `messages`.
            "step_count": 0,
            "mode": mode,
        }

        async for event in self._graph.astream(
            graph_input, config, stream_mode="custom"
        ):
            if isinstance(event, DoneEvent):
                event.data.setdefault(
                    "duration_ms", int((time.monotonic() - started) * 1000)
                )
            yield event
