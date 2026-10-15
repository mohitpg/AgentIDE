"""agentide-telemetry: OpenTelemetry → OpenObserve bootstrap shared by every service."""

from telemetry.config import TelemetrySettings, get_telemetry_settings
from telemetry.llm import LLMTelemetryCallback, llm_callbacks
from telemetry.setup import setup_telemetry

__all__ = [
    "LLMTelemetryCallback",
    "TelemetrySettings",
    "get_telemetry_settings",
    "llm_callbacks",
    "setup_telemetry",
]
