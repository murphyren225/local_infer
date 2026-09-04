"""Exact-match response cache with LRU eviction and TTL.

Only near-deterministic requests are cacheable (temperature below
``cache.max_temperature``, non-streaming). Semantic caching is a v0.3
roadmap item; exact matching alone already absorbs the repeated batch
prompts this gateway targets (same template applied to many inputs
often repeats headers/instructions verbatim).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

_KEY_FIELDS = (
    "messages",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "response_format",
    "stop",
    "seed",
)


def cacheable(payload: dict[str, Any], cache_cfg: dict[str, Any]) -> bool:
    if not cache_cfg.get("enabled", True):
        return False
    if payload.get("stream"):
        return False
    if payload.get("tools") or payload.get("functions"):
        return False
    # OpenAI default temperature is 1.0 — absent means non-deterministic.
    temperature = payload.get("temperature")
    if temperature is None:
        return False
    return float(temperature) <= float(cache_cfg.get("max_temperature", 0.2))


def cache_key(lane: str, payload: dict[str, Any]) -> str:
    material = {k: payload.get(k) for k in _KEY_FIELDS if k in payload}
    material["_lane"] = lane
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, max_entries: int = 10000, ttl_seconds: float = 3600):
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str, now: float | None = None) -> Any | None:
        now = time.monotonic() if now is None else now
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if now - stored_at > self.ttl_seconds:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def put(self, key: str, value: Any, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._store[key] = (now, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)
