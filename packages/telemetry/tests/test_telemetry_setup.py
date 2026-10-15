"""Settings, the disabled path, and the orphan-client-span sampler."""

from __future__ import annotations

import base64

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision, ParentBased
from opentelemetry.trace import SpanKind
from telemetry import TelemetrySettings, setup_telemetry
from telemetry.setup import DropOrphanClientSpans


def _settings(**overrides: object) -> TelemetrySettings:
    return TelemetrySettings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_otlp_headers_are_basic_auth() -> None:
    settings = _settings(openobserve_email="root@example.com", openobserve_password="s3cret#1")
    header = settings.otlp_headers()["Authorization"]
    scheme, _, token = header.partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "root@example.com:s3cret#1"


def test_auth_token_is_used_as_is_and_wins() -> None:
    settings = _settings(
        openobserve_email="root@example.com",
        openobserve_password="ignored",
        openobserve_auth_token="cm9vdDpwdw==",
    )
    assert settings.otlp_headers() == {"Authorization": "Basic cm9vdDpwdw=="}
    pasted = _settings(openobserve_auth_token="Basic cm9vdDpwdw==")
    assert pasted.otlp_headers() == {"Authorization": "Basic cm9vdDpwdw=="}


def test_otlp_headers_empty_without_credentials() -> None:
    assert _settings(openobserve_email="root@example.com").otlp_headers() == {}


def test_base_url_drops_trailing_slash() -> None:
    settings = _settings(otel_exporter_otlp_endpoint="http://localhost:5080/api/default/")
    assert settings.otlp_base_url == "http://localhost:5080/api/default"


def test_disabled_by_default_and_setup_is_a_noop() -> None:
    settings = _settings()
    assert settings.otel_enabled is False
    assert setup_telemetry("agentide-test", settings=settings) is False


def test_sampler_drops_only_parentless_client_spans() -> None:
    sampler = DropOrphanClientSpans()
    assert sampler.should_sample(None, 1, "SELECT", kind=SpanKind.CLIENT).decision == Decision.DROP
    for kind in (SpanKind.SERVER, SpanKind.INTERNAL, SpanKind.CONSUMER, SpanKind.PRODUCER):
        result = sampler.should_sample(None, 1, "op", kind=kind)
        assert result.decision == Decision.RECORD_AND_SAMPLE


def test_client_spans_under_a_kept_root_are_exported() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ParentBased(root=DropOrphanClientSpans()))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("heartbeat-query", kind=SpanKind.CLIENT):
        pass  # orphan: dropped
    with (
        tracer.start_as_current_span("chat.turn"),
        tracer.start_as_current_span("chat model", kind=SpanKind.CLIENT),
    ):
        pass  # has a parent: kept

    assert sorted(s.name for s in exporter.get_finished_spans()) == ["chat model", "chat.turn"]
