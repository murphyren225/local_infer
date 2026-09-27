"""RL routing policy — the slot where the learned router lives.

Where RL sits in the chain (docs/pipeline-design.md §11): Switchyard(fork) → decider(:4100, this
service) → PAIR → engine. Switchyard sends a DecisionState every turn and applies whatever
Decision comes back; PAIR and the engines never see the policy. So "搭 RL" means: train a policy
on the traces Switchyard already writes, load it here as `--backend rl`, keep `rules` as the
floor the service falls back to. Nothing else in the chain changes.

Data: logs/rl/*.json — one file per request written by Switchyard's RL logger (fork adds
`decision` = what the decider said and `served_tier` = what actually served it). Reward is
attached offline (tools/rl/label.py, to be written when a quality signal exists: user accept /
harness success / latency budget). Until then this backend is a scaffold: it loads a linear
policy from a JSON file and, when no policy file exists, delegates to the rules backend so the
service stays correct.

Policy file (decider/policy.json): {"features": [...names...], "weights": {"strong": [...],
"cloud": [...]}, "bias": {"strong": b, "cloud": b}}. Route probabilities are a softmax over
{weak: 0, strong: w·x + b, cloud: w·x + b}. Features are cheap, deterministic functions of the
DecisionState so the same code computes them at train and serve time (see features()).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .service import RulesBackend, TIERS, normalize

POLICY_FILE = Path(os.environ.get("DECIDER_POLICY", Path(__file__).with_name("policy.json")))

STRONG_HINTS = RulesBackend.STRONG_HINTS
FEATURES = ("turn", "len_first_user", "len_summary", "has_tools", "strong_hint", "tt_code", "tt_agent",
            "tt_edit", "tt_qa", "tt_extract")


def features(state: dict) -> list[float]:
    """Deterministic feature vector for a DecisionState (also used by the trainer)."""
    text = ((state.get("first_user_text") or "") + " " + (state.get("summary") or "")).lower()
    tt = state.get("task_type") or ""
    return [
        float(min(state.get("turn") or 1, 20)) / 20.0,
        math.log1p(len(state.get("first_user_text") or "")) / 10.0,
        math.log1p(len(state.get("summary") or "")) / 10.0,
        1.0 if state.get("has_tools") else 0.0,
        1.0 if any(h in text for h in STRONG_HINTS) else 0.0,
        1.0 if tt == "code" else 0.0,
        1.0 if tt == "agent" else 0.0,
        1.0 if tt == "edit" else 0.0,
        1.0 if tt == "qa" else 0.0,
        1.0 if tt == "extract" else 0.0,
    ]


def load_policy(path: Path = POLICY_FILE) -> dict | None:
    try:
        p = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if p.get("features") != list(FEATURES):
        return None  # trained against another feature set; refuse rather than misroute
    return p


class RLBackend:
    """Linear softmax policy over features(); rules backend when no policy is trained yet."""
    name = "rl"

    def __init__(self, path: Path = POLICY_FILE) -> None:
        self.policy = load_policy(path)
        self.fallback = RulesBackend()

    def decide(self, state: dict) -> dict:
        if not self.policy:
            d = self.fallback.decide(state)
            d["provider"] = "rl"
            d["reason"] = "no policy file; rules fallback (" + d.get("reason", "") + ")"
            return d
        x = features(state)
        logits = {"weak": 0.0}
        for tier in ("strong", "cloud"):
            w = self.policy["weights"].get(tier, [0.0] * len(x))
            logits[tier] = sum(a * b for a, b in zip(w, x)) + float(self.policy["bias"].get(tier, 0.0))
        m = max(logits.values())
        exp = {t: math.exp(v - m) for t, v in logits.items()}
        route = normalize({t: exp[t] for t in TIERS})
        top = max(route, key=route.get)
        task_type = state.get("task_type") or self.fallback.decide(state)["task_type"]
        return {"route": route, "confidence": route[top], "task_type": task_type, "rewrite": None,
                "reason": f"rl policy {self.policy.get('version', '?')}", "provider": self.name}
