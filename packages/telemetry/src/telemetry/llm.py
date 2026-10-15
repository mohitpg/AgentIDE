"""
LangChain callback: one OTel span + GenAI metrics per chat-model call.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry import metrics, trace
from opentelemetry.trace import Span, SpanKind, StatusCode

_tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
_token_usage = _meter.create_histogram(
    "gen_ai.client.token.usage", unit="{token}", description="Input and output tokens per LLM call"
)
_duration = _meter.create_histogram(
    "gen_ai.client.operation.duration", unit="s", description="LLM call duration"
)


class LLMTelemetryCallback(BaseCallbackHandler):
    """Times each chat-model run (keyed by LangChain's `run_id`) as a span."""

    # Run in the caller's task rather than a thread pool, so the span parents to
    # the active graph-node / agent span.
    run_inline = True

    def __init__(self) -> None:
        self._runs: dict[UUID, tuple[Span, float, dict[str, str]]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        metadata = metadata or {}
        params = kwargs.get("invocation_params") or {}
        model = str(metadata.get("ls_model_name") or params.get("model") or "unknown")
        # Low-cardinality attributes shared by the span and both metrics.
        common = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": str(metadata.get("ls_provider") or "unknown"),
            "gen_ai.request.model": model,
        }
        attributes: dict[str, Any] = dict(common)
        if metadata.get("ls_temperature") is not None:
            attributes["gen_ai.request.temperature"] = metadata["ls_temperature"]
        if metadata.get("ls_max_tokens") is not None:
            attributes["gen_ai.request.max_tokens"] = metadata["ls_max_tokens"]
        span = _tracer.start_span(f"chat {model}", kind=SpanKind.CLIENT, attributes=attributes)
        self._runs[run_id] = (span, time.perf_counter(), common)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        span, started, common = run
        attributes = _response_attributes(response)
        span.set_attributes(attributes)
        span.end()
        _duration.record(time.perf_counter() - started, common)
        for token_type, key in (
            ("input", "gen_ai.usage.input_tokens"),
            ("output", "gen_ai.usage.output_tokens"),
        ):
            if key in attributes:
                _token_usage.record(attributes[key], {**common, "gen_ai.token.type": token_type})

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        span, started, common = run
        error_type = type(error).__qualname__
        span.record_exception(error)
        span.set_attribute("error.type", error_type)
        span.set_status(StatusCode.ERROR, str(error))
        span.end()
        _duration.record(time.perf_counter() - started, {**common, "error.type": error_type})


def _response_attributes(response: LLMResult) -> dict[str, Any]:
    """Token usage, response model and finish reason from the first generation."""
    first = response.generations[0] if response.generations else []
    generation = first[0] if first else None
    message = getattr(generation, "message", None)
    info = {
        **(getattr(generation, "generation_info", None) or {}),
        **(getattr(message, "response_metadata", None) or {}),
    }

    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:  # providers that only report usage in `llm_output`
        token_usage = (response.llm_output or {}).get("token_usage") or {}
        usage = {
            "input_tokens": token_usage.get("prompt_tokens"),
            "output_tokens": token_usage.get("completion_tokens"),
        }

    attributes: dict[str, Any] = {}
    if usage.get("input_tokens") is not None:
        attributes["gen_ai.usage.input_tokens"] = usage["input_tokens"]
    if usage.get("output_tokens") is not None:
        attributes["gen_ai.usage.output_tokens"] = usage["output_tokens"]
    if info.get("model_name"):
        attributes["gen_ai.response.model"] = info["model_name"]
    if info.get("finish_reason"):
        attributes["gen_ai.response.finish_reasons"] = [info["finish_reason"]]
    return attributes


_callback = LLMTelemetryCallback()


def llm_callbacks() -> list[BaseCallbackHandler]:
    """Callbacks for a chat model's constructor (one handler shared per process)."""
    return [_callback]
