"""Settings parsing that no router test covers."""

from __future__ import annotations

from api.config import Settings


def test_cors_origins_are_split_and_trimmed():
    settings = Settings(cors_origins="app://agentide, http://localhost:5173,")
    assert settings.cors_origin_list == ["app://agentide", "http://localhost:5173"]
