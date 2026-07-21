"""
RAG agent — retrieve → rerank → cite → answer.

Unlike the Code/Summarizer agents (which use the default LLM-only `run`), this
agent overrides `run` to first retrieve grounding context from pgvector via the
shared `RagPipeline` (hybrid HNSW + full-text search, cross-encoder rerank), then
answer with citations.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from agent_common.base import BaseAgent
from agent_common.llm import build_chat_model
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from rag import RagConfig, RagPipeline, SearchHit

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel
    from sqlalchemy.ext.asyncio import AsyncSession

_SKILLS_DIR = Path(__file__).parent / "skills"
_DEFAULT_DB_URL = "postgresql+asyncpg://agentide:agentide@localhost:5433/agentide"


def _config_from_env() -> RagConfig:
    return RagConfig(
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", RagConfig.embedding_model),
        embedding_dim=int(os.getenv("RAG_EMBEDDING_DIM", RagConfig.embedding_dim)),
        reranker_model=os.getenv("RAG_RERANKER_MODEL", RagConfig.reranker_model),
        chunk_tokens=int(os.getenv("RAG_CHUNK_TOKENS", RagConfig.chunk_tokens)),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", RagConfig.chunk_overlap)),
        candidate_k=int(os.getenv("RAG_CANDIDATE_K", RagConfig.candidate_k)),
        top_k=int(os.getenv("RAG_TOP_K", RagConfig.top_k)),
    )


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    out = ""
    for block in content or []:  # type: ignore[union-attr]
        if isinstance(block, dict) and block.get("type") == "text":
            out += block.get("text", "")
    return out


def format_context(hits: list[SearchHit]) -> tuple[str, str]:
    """Render passages for the prompt and a matching `Sources:` footer.

    Returns ``(context, sources)``; both use the same 1-based numbering so the
    model's inline `[n]` citations line up with the footer entries.
    """
    context_parts: list[str] = []
    source_parts: list[str] = []
    for n, hit in enumerate(hits, start=1):
        context_parts.append(f"[{n}] ({hit.filename} #{hit.chunk_index})\n{hit.content}")
        source_parts.append(f"[{n}] {hit.filename} (chunk {hit.chunk_index})")
    return "\n\n".join(context_parts), "Sources:\n" + "\n".join(source_parts)


_NO_CONTEXT = (
    "I couldn't find anything relevant in the indexed documents. "
    "Try uploading a document first, or rephrasing the question."
)


class RagAgent(BaseAgent):
    id = "rag"
    name = "RAG"
    description = "Answers questions grounded in indexed documents, with citations."

    def __init__(
        self,
        *,
        pipeline: RagPipeline | None = None,
        chat_model: BaseChatModel | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
        database_url: str | None = None,
        **kwargs: object,
    ) -> None:
        kwargs.setdefault("url", "http://localhost:8103")
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._pipeline = pipeline or RagPipeline(_config_from_env())
        self._chat_model = chat_model
        self._session_factory = session_factory
        self._database_url = database_url or os.getenv("DATABASE_URL", _DEFAULT_DB_URL)
        self._sessionmaker: object | None = None

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    # ── DB session (lazy async engine, or injected factory for tests) ──────────
    def _session(self) -> AsyncSession:
        if self._session_factory is not None:
            return self._session_factory()
        if self._sessionmaker is None:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(self._database_url, pool_pre_ping=True)
            self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        return self._sessionmaker()  # type: ignore[operator]

    # ── execution ──────────────────────────────────────────────────────────────
    async def run(
        self, skill_id: str, user_input: str, *, run_id: str = ""
    ) -> AsyncIterator[str]:
        cfg = self._pipeline.config
        async with self._session() as session:
            hits = await self._pipeline.search(
                session, user_input, top_k=cfg.top_k, candidate_k=cfg.candidate_k
            )

        if not hits:
            yield _NO_CONTEXT
            return

        context, sources = format_context(hits)

        # `retrieve` returns the ranked passages verbatim — no LLM synthesis.
        if skill_id == "retrieve":
            yield context
            return

        skill = self.get_skill("answer") or self._skills[self.default_skill_id()]
        model = self._chat_model or build_chat_model(self._llm_config, streaming=True)
        user_prompt = (
            f"Context passages:\n\n{context}\n\n"
            f"Question: {user_input}\n\n"
            "Answer using ONLY the context above. Cite the passages you use inline "
            "as [n]. If the answer isn't in the context, say so."
        )
        messages = [SystemMessage(content=skill.prompt), HumanMessage(content=user_prompt)]
        async for chunk in model.astream(messages):
            if isinstance(chunk, AIMessageChunk):
                piece = _text(chunk.content)
                if piece:
                    yield piece

        # Append the citation footer so the UI always shows what grounded the answer.
        yield "\n\n" + sources
