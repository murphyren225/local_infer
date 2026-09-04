"""Routing policy — turns a request into a lane decision.

Order of precedence:
1. Explicit override — client sets ``model`` to ``"small"``/``"large"``
   (or a lane's real model id). The gateway never second-guesses an
   explicit choice.
2. Heuristic assessment (see heuristics.py) for ``"auto"`` / anything else.

Tier-2 routing (small-model difficulty pre-judging) plugs in here later
without touching callers — see docs/routing.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .heuristics import Signal, assess


@dataclass
class Decision:
    lane: str                    # "small" | "large"
    reason: str                  # short, log-friendly explanation
    score: int = 0
    signals: list[Signal] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "reason": self.reason,
            "score": self.score,
            "signals": [
                {"name": s.name, "weight": s.weight, "detail": s.detail}
                for s in self.signals
            ],
        }


def decide(payload: dict[str, Any], cfg: dict[str, Any]) -> Decision:
    requested = str(payload.get("model") or "auto")
    lanes: dict[str, Any] = cfg.get("lanes", {})

    if requested in lanes:
        return Decision(requested, "explicit-lane")
    for lane_name, lane_cfg in lanes.items():
        if requested == lane_cfg.get("model"):
            return Decision(lane_name, "explicit-model")

    assessment = assess(payload, cfg.get("routing", {}))
    return Decision(
        assessment.lane,
        f"heuristic:{assessment.reason}",
        assessment.score,
        assessment.signals,
    )
