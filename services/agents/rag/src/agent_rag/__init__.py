"""
RAG agent — skills: answer (retrieve→rerank→cite→answer), retrieve.

An independent A2A service that grounds answers in the pgvector corpus via the
shared `agentide-rag` pipeline (hybrid vector + full-text search, cross-encoder
rerank). Implemented in Phase 4.
"""

from agent_rag.agent import RagAgent, format_context

__all__ = ["RagAgent", "format_context"]
