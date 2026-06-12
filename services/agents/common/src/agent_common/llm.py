"""
Shared LLM client for A2A agents.

Mirrors `orchestrator.llm` but lives here so specialist agents never import the
orchestrator (they are independent services). Both build the same OpenRouter
chat model so behaviour is identical across the stack.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import SystemMessage
from langchain_openrouter import ChatOpenRouter

from agent_common.config import AgentSettings


@dataclass(frozen=True)
class LLMConfig:
    """Everything the LLM client needs; built from AgentSettings or injected in tests."""

    api_key: str
    model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    max_tokens: int = 4096
    temperature: float = 0.3

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> LLMConfig:
        return cls(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )


def build_chat_model(config: LLMConfig, *, streaming: bool = True) -> ChatOpenRouter:
    kwargs: dict = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "streaming": streaming,
        "stream_usage": True,
    }
    if config.api_key:
        kwargs["api_key"] = config.api_key
    return ChatOpenRouter(**kwargs)


def system_message(prompt: str) -> SystemMessage:
    return SystemMessage(content=prompt)
