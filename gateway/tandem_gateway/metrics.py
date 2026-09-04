"""In-process request accounting.

Answers the question every buyer asks first: "这台机器到底帮我省了多少钱?"
Token counts come from backend ``usage`` blocks; the would-have-cost figure
uses the reference cloud prices in config (estimation only, not billing).

In-memory on purpose for v0.1 — restarting the gateway resets stats.
Durable metrics (Prometheus export) is a v0.2 roadmap item.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LaneStats:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0


@dataclass
class Stats:
    started_at: float = field(default_factory=time.time)
    lanes: dict[str, LaneStats] = field(default_factory=dict)
    cache_hits: int = 0
    escalations: int = 0
    decisions: dict[str, int] = field(default_factory=dict)  # reason -> count

    def lane(self, name: str) -> LaneStats:
        return self.lanes.setdefault(name, LaneStats())

    def record_decision(self, reason: str) -> None:
        # keep only the coarse reason family, not per-request detail
        family = reason.split(":", 1)[0]
        self.decisions[family] = self.decisions.get(family, 0) + 1

    def record_usage(self, lane: str, usage: dict[str, Any] | None) -> None:
        stats = self.lane(lane)
        stats.requests += 1
        if usage:
            stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            stats.completion_tokens += int(usage.get("completion_tokens") or 0)

    def snapshot(self, pricing: dict[str, Any]) -> dict[str, Any]:
        input_rate = float(pricing.get("input_per_m", 0.0))
        output_rate = float(pricing.get("output_per_m", 0.0))
        total_prompt = sum(s.prompt_tokens for s in self.lanes.values())
        total_completion = sum(s.completion_tokens for s in self.lanes.values())
        would_have_cost = (
            total_prompt / 1_000_000 * input_rate
            + total_completion / 1_000_000 * output_rate
        )
        return {
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "lanes": {
                name: {
                    "requests": s.requests,
                    "prompt_tokens": s.prompt_tokens,
                    "completion_tokens": s.completion_tokens,
                    "errors": s.errors,
                }
                for name, s in self.lanes.items()
            },
            "cache_hits": self.cache_hits,
            "escalations": self.escalations,
            "decision_reasons": dict(self.decisions),
            "estimated_cloud_cost_usd": round(would_have_cost, 4),
        }
