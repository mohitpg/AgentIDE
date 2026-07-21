"""
Self-registration with the gateway agent registry.

On startup an agent POSTs its A2A Agent Card to ``${gateway_url}/agents/register``,
then heartbeats every `heartbeat_seconds` so the registry knows it's live; on
shutdown it deregisters. If the registry forgot it (e.g. the gateway restarted),
a heartbeat that comes back 404 triggers a re-register.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx

logger = logging.getLogger(__name__)


class RegistrationManager:
    def __init__(
        self,
        *,
        agent_id: str,
        card: dict,
        gateway_url: str,
        url: str | None = None,
        heartbeat_seconds: int = 30,
        auth_token: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._card = card
        self._url = url or card.get("url")
        self._gateway = gateway_url.rstrip("/")
        self._interval = max(1, heartbeat_seconds)
        self._auth_token = auth_token
        self._transport = transport  # injectable for tests
        self._task: asyncio.Task | None = None

    # ── http helpers ─────────────────────────────────────────────────────────
    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = (
            {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else {}
        )
        return httpx.AsyncClient(
            base_url=self._gateway, timeout=timeout, headers=headers,
            transport=self._transport,
        )

    async def register(self) -> None:
        async with self._client(timeout=10.0) as client:
            resp = await client.post(
                "/agents/register",
                json={"agent_id": self._agent_id, "card": self._card, "url": self._url},
            )
            resp.raise_for_status()

    async def deregister(self) -> None:
        async with self._client(timeout=5.0) as client:
            await client.delete(f"/agents/{self._agent_id}")

    async def _send_heartbeat(self) -> None:
        async with self._client(timeout=10.0) as client:
            resp = await client.post(f"/agents/{self._agent_id}/heartbeat")
            if resp.status_code == httpx.codes.NOT_FOUND:
                # Registry lost us (gateway restart?) — re-establish.
                logger.info("registry lost agent %s; re-registering", self._agent_id)
                await self.register()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._send_heartbeat()
            except Exception as exc:  # keep beating despite transient failures
                logger.warning("heartbeat for %s failed: %s", self._agent_id, exc)

    async def start(self) -> None:
        try:
            await self.register()
            logger.info("registered agent %s with %s", self._agent_id, self._gateway)
        except Exception as exc:  # gateway down — heartbeat loop will retry
            logger.warning(
                "initial registration for %s failed (%s); will retry via heartbeat",
                self._agent_id,
                exc,
            )
        self._task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        try:
            await self.deregister()
        except Exception as exc:
            logger.warning("deregister for %s failed: %s", self._agent_id, exc)
