"""Routing policy — turns a request into a lane decision.

Order of precedence (docs/agent-interface.md §2.3):
1. Explicit override — client sets ``model`` to ``"small"``/``"large"``
   (or a lane's real model id). The gateway never second-guesses an
   explicit choice.
2. Hard rules — tools present, or context beyond the small lane's
   physical capacity. These override even an agent's hint (the response
   then says ``hint-overridden:<rule>`` so the caller can learn).
3. Agent hint — ``x-tandem-hint`` step-type words; unknown values are
   silently ignored for forward compatibility.
4. Heuristic assessment (see heuristics.py) for everything else.

Tier-2 routing (small-model difficulty pre-judging) plugs in here later
without touching callers — see docs/routing.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .heuristics import Signal, assess

HINT_LANES = {
    "plan": "large",
    "reason": "large",
    "code": "large",
    "large": "large",
    "digest": "small",
    "format": "small",
    "classify": "small",
    "extract": "small",
    "chat": "small",
    "small": "small",
}


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


def decide(
    payload: dict[str, Any], cfg: dict[str, Any], hint: str | None = None
) -> Decision:
    requested = str(payload.get("model") or "auto")
    lanes: dict[str, Any] = cfg.get("lanes", {})

    if requested in lanes:
        return Decision(requested, "explicit-lane")
    for lane_name, lane_cfg in lanes.items():
        if requested == lane_cfg.get("model"):
            return Decision(lane_name, "explicit-model")

    assessment = assess(payload, cfg.get("routing", {}))

    hint_lane = HINT_LANES.get(hint.strip().lower()) if hint else None
    if hint_lane:
        hard = next((s for s in assessment.signals if s.weight >= 99), None)
        if hard and assessment.lane != hint_lane:
            return Decision(
                assessment.lane,
                f"hint-overridden:{hard.name}",
                assessment.score,
                assessment.signals,
            )
        return Decision(
            hint_lane, f"hint:{hint.strip().lower()}", assessment.score, assessment.signals
        )

    return Decision(
        assessment.lane,
        f"heuristic:{assessment.reason}",
        assessment.score,
        assessment.signals,
    )
