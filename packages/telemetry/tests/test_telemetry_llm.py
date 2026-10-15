"""The LangChain → OTel callback: span shape, metrics, errors, no content."""

from __future__ import annotations

import uuid

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from telemetry import llm
from telemetry.llm import LLMTelemetryCallback

_METADATA = {
    "ls_provider": "openrouter",
    "ls_model_name": "vendor/model-x",
    "ls_temperature": 0.3,
    "ls_max_tokens": 256,
}


@pytest.fixture
def tracing(monkeypatch: pytest.MonkeyPatch) -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm, "_tracer", provider.get_tracer("test"))
    return provider, exporter


@pytest.fixture
def metric_reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    for attr, name in (
        ("_token_usage", "gen_ai.client.token.usage"),
        ("_duration", "gen_ai.client.operation.duration"),
    ):
        monkeypatch.setattr(llm, attr, meter.create_histogram(name))
    return reader


def _points(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    return [
        point
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _result(text: str) -> LLMResult:
    message = AIMessage(
        content=text,
        usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
        response_metadata={"model_name": "vendor/model-x-2026", "finish_reason": "stop"},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_records_genai_span_and_metrics_without_content(tracing, metric_reader) -> None:
    _, exporter = tracing
    callback, run_id = LLMTelemetryCallback(), uuid.uuid4()

    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata=_METADATA)
    callback.on_llm_end(_result("the secret answer"), run_id=run_id)

    (span,) = exporter.get_finished_spans()
    assert span.name == "chat vendor/model-x"
    assert span.kind == SpanKind.CLIENT
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "openrouter"
    assert attrs["gen_ai.request.model"] == "vendor/model-x"
    assert attrs["gen_ai.request.temperature"] == 0.3
    assert attrs["gen_ai.request.max_tokens"] == 256
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 7
    assert attrs["gen_ai.response.model"] == "vendor/model-x-2026"
    assert attrs["gen_ai.response.finish_reasons"] == ("stop",)
    assert not any("secret" in str(v) for v in attrs.values())

    usage = _points(metric_reader, "gen_ai.client.token.usage")
    assert {p.attributes["gen_ai.token.type"]: p.sum for p in usage} == {"input": 11, "output": 7}
    assert len(_points(metric_reader, "gen_ai.client.operation.duration")) == 1


def test_error_marks_span_failed(tracing, metric_reader) -> None:
    _, exporter = tracing
    callback, run_id = LLMTelemetryCallback(), uuid.uuid4()

    callback.on_chat_model_start({}, [[]], run_id=run_id, metadata=_METADATA)
    callback.on_llm_error(TimeoutError("upstream timed out"), run_id=run_id)

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["error.type"] == "TimeoutError"
    (point,) = _points(metric_reader, "gen_ai.client.operation.duration")
    assert point.attributes["error.type"] == "TimeoutError"


def test_unknown_run_is_ignored(tracing) -> None:
    _, exporter = tracing
    LLMTelemetryCallback().on_llm_end(_result("x"), run_id=uuid.uuid4())
    assert exporter.get_finished_spans() == ()


async def test_model_call_nests_under_the_active_span(tracing) -> None:
    provider, exporter = tracing
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content="hello")]), callbacks=[LLMTelemetryCallback()]
    )

    with provider.get_tracer("test").start_as_current_span("supervisor"):
        await model.ainvoke("hi")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    chat = next(s for name, s in spans.items() if name.startswith("chat "))
    assert chat.parent is not None
    assert chat.parent.span_id == spans["supervisor"].context.span_id
