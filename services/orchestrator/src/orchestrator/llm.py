"""
Anthropic chat-model client.

Kept decoupled from the API layer: callers pass an `LLMConfig` (the API builds
one from its Settings) so the orchestrator never imports `api.config`.

Prompt caching: when enabled, the system prompt is sent as a cache-controlled
content block, so Anthropic reuses it across turns instead of re-billing it.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import SystemMessage
from langchain_openrouter import ChatOpenRouter
from telemetry import llm_callbacks

SYSTEM_PROMPT = (
    "You are AgentIDE, a helpful coding assistant embedded in a desktop IDE. "
    "Answer clearly and concisely. When you write code, use fenced code blocks "
    "with the correct language tag. If you are unsure, say so rather than guessing."
)


@dataclass(frozen=True)
class LLMConfig:
    """Everything the LLM client needs, injected from the API's Settings."""

    api_key: str
    model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    max_tokens: int = 4096
    temperature: float = 0.7


def build_chat_model(config: LLMConfig, *, streaming: bool = True) -> ChatOpenRouter:
    kwargs: dict = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "streaming": streaming,
        "stream_usage": True,
        "callbacks": llm_callbacks(),  # a `chat {model}` span + token metrics per call
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    return ChatOpenRouter(**kwargs)


def system_message(config: LLMConfig) -> SystemMessage:
    """System prompt"""
    return SystemMessage(content=SYSTEM_PROMPT)
