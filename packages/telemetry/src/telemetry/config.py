"""
Telemetry settings, loaded from environment / repo-root .env.

Every process (gateway, agents, workers) reads the same values, so one .env
points the whole stack at one OpenObserve. Exporting is off unless
`OTEL_ENABLED=true`, so tests and a fresh checkout never try to reach it.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py → telemetry → src → telemetry → packages → <repo root>
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class TelemetrySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    otel_enabled: bool = False
    # OpenObserve's OTLP/HTTP base for one organization; the exporters append
    # /v1/traces, /v1/metrics and /v1/logs. No trailing slash.
    otel_exporter_otlp_endpoint: str = "http://localhost:5080/api/default"
    # Basic-auth credentials for an OpenObserve user allowed to ingest: either
    # email + password, or the ready-made token from OpenObserve's
    # Data sources > Custom > OTLP page (the value after "Basic "), which wins.
    openobserve_email: str = ""
    openobserve_password: str = ""
    openobserve_auth_token: str = ""
    # Metric push interval in ms (same name and meaning as the standard OTel var).
    otel_metric_export_interval: int = 15_000
    # Minimum level of Python log records shipped to OpenObserve.
    log_level: str = "INFO"
    app_env: str = "development"

    @property
    def otlp_base_url(self) -> str:
        return self.otel_exporter_otlp_endpoint.rstrip("/")

    def otlp_headers(self) -> dict[str, str]:
        """Basic-auth header OpenObserve expects; empty if credentials are unset."""
        if self.openobserve_auth_token:
            token = self.openobserve_auth_token.strip()
            return {"Authorization": token if token.startswith("Basic ") else f"Basic {token}"}
        if not (self.openobserve_email and self.openobserve_password):
            return {}
        raw = f"{self.openobserve_email}:{self.openobserve_password}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}


@lru_cache
def get_telemetry_settings() -> TelemetrySettings:
    return TelemetrySettings()
