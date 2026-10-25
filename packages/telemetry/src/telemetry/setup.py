"""
One-call OpenTelemetry bootstrap for every AgentIDE process.

`setup_telemetry("agentide-api")` wires all three signals to OpenObserve over
OTLP/HTTP (protobuf):

  traces  → BatchSpanProcessor             → {base}/v1/traces
  metrics → PeriodicExportingMetricReader  → {base}/v1/metrics
  logs    → root `logging` handler → BatchLogRecordProcessor → {base}/v1/logs
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from collections.abc import Sequence

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import Decision, ParentBased, Sampler, SamplingResult
from opentelemetry.trace import Link, SpanKind, TraceState
from opentelemetry.util.types import Attributes

from telemetry.config import TelemetrySettings, get_telemetry_settings

logger = logging.getLogger(__name__)

# Chatty INFO loggers (httpx logs every request line) held at WARNING once the
# root logger is lowered to ship INFO records.
_QUIET_LOGGERS = ("httpx", "httpcore")

_configured = False


class DropOrphanClientSpans(Sampler):
    """Root sampler: drop client spans that have no parent operation.

    A DB query, Redis command or HTTP call with no surrounding request/task span
    is background housekeeping — agent heartbeats, `/health` probes, the task
    hub's pub/sub bookkeeping — and would otherwise flood OpenObserve with
    one-span traces. Every other root span (requests, chat turns, Celery tasks)
    is kept, and `ParentBased` keeps all children of a kept root.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        if kind == SpanKind.CLIENT:
            return SamplingResult(Decision.DROP)
        return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes)

    def get_description(self) -> str:
        return "DropOrphanClientSpans"


class _DropOwnLogs(logging.Filter):
    """Keep the SDK's own records out of the OTLP pipeline.

    Otherwise an unreachable OpenObserve would feed itself "export failed"
    warnings forever.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


def setup_telemetry(service_name: str, *, settings: TelemetrySettings | None = None) -> bool:
    """Configure this process's tracer/meter/logger providers. True if exporting."""
    global _configured
    if _configured:
        return True
    settings = settings or get_telemetry_settings()
    if not settings.otel_enabled:
        return False

    base = settings.otlp_base_url
    headers = settings.otlp_headers()
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "agentide",
            "deployment.environment.name": settings.app_env,
        }
    )

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(root=DropOrphanClientSpans())
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces", headers=headers))
    )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics", headers=headers),
        export_interval_millis=settings.otel_metric_export_interval,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs", headers=headers))
    )
    set_logger_provider(logger_provider)
    _install_log_handler(logger_provider, settings.log_level)

    _instrument_libraries()
    _configured = True

    if not headers:
        logger.warning(
            "OTEL_ENABLED is set but neither OPENOBSERVE_EMAIL / OPENOBSERVE_PASSWORD "
            "nor OPENOBSERVE_AUTH_TOKEN is; OpenObserve will reject every export (401)."
        )
    logger.info("telemetry for %s exporting to %s", service_name, base)
    return True


def _install_log_handler(provider: LoggerProvider, level_name: str) -> None:
    level = logging.getLevelNamesMapping().get(level_name.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        # Adding a handler switches off logging's stderr fallback; keep the
        # console showing what it showed before (WARNING+).
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(console)

    handler = LoggingHandler(level=level, logger_provider=provider)
    handler.addFilter(_DropOwnLogs())
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)
    for name in _QUIET_LOGGERS:
        quiet = logging.getLogger(name)
        if quiet.level == logging.NOTSET:
            quiet.setLevel(logging.WARNING)


# (library, instrumentor module, instrumentor class). httpx propagates
# `traceparent` on A2A calls, so agent spans join the turn's trace; Celery does
# the same through task headers (gateway → worker).
_INSTRUMENTED_LIBRARIES = (
    ("httpx", "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
    ("asyncpg", "opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor"),
    ("redis", "opentelemetry.instrumentation.redis", "RedisInstrumentor"),
    ("celery", "opentelemetry.instrumentation.celery", "CeleryInstrumentor"),
)


def _instrument_libraries() -> None:
    """Client-side auto-instrumentation shared by every service.

    Imported lazily so `import telemetry` stays cheap. Each instrumentor imports
    the library it wraps, and a service's image only has its own dependencies
    (the agents have no asyncpg, Redis or Celery), so missing libraries are
    skipped.
    """
    for library, module, instrumentor in _INSTRUMENTED_LIBRARIES:
        if importlib.util.find_spec(library) is None:
            continue
        getattr(importlib.import_module(module), instrumentor)().instrument()
