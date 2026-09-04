"""Upstream lane backends (vLLM or any OpenAI-compatible server).

The gateway never talks to models directly — each lane is just a base_url
plus the real model id to substitute. This keeps the whole stack testable
without a GPU: point a lane at any OpenAI-compatible endpoint.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import httpx


class LaneBackend:
    def __init__(self, name: str, base_url: str, model: str, client: httpx.AsyncClient):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = client

    def _prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(payload)
        prepared["model"] = self.model
        return prepared

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=self._prepare(payload),
        )
        response.raise_for_status()
        return response.json()

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=self._prepare(payload),
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk


def build_backends(cfg: dict[str, Any], client: httpx.AsyncClient) -> dict[str, LaneBackend]:
    return {
        name: LaneBackend(name, lane["base_url"], lane["model"], client)
        for name, lane in cfg.get("lanes", {}).items()
    }
