"""The registry → supervisor discovery bridge builds an AgentDirectory."""

from __future__ import annotations

from api.services.discovery import build_directory


class FakeRegistry:
    def __init__(self, urls: dict[str, str]) -> None:
        self._urls = urls

    async def url_map(self) -> dict[str, str]:
        return dict(self._urls)


async def test_build_directory_from_registry():
    registry = FakeRegistry({"code": "http://localhost:8102"})
    directory = await build_directory(registry)
    assert directory.url_for("code") == "http://localhost:8102"
    assert directory.known_agents() == ["code"]


async def test_empty_registry_yields_empty_directory():
    directory = await build_directory(FakeRegistry({}))
    assert directory.known_agents() == []
    assert directory.url_for("code") is None
