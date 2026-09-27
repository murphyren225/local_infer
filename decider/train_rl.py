"""Build the routing dataset from Switchyard's RL traces and fit the linear policy.

    python3 -m decider.train_rl                 # dataset only → logs/rl-dataset.jsonl
    python3 -m decider.train_rl --labels logs/rl-labels.jsonl --fit   # + fit → decider/policy.json

Rows come from logs/rl/*.json (one per request; the fork adds `decision` and `served_tier`).
A row is trainable only once it has a reward. Rewards are NOT in the traces yet: they need a
quality signal that does not exist in this pipeline today (user acceptance, harness task
success, latency budget). `--labels` takes a JSONL of {"uuid": ..., "reward": float in [0,1]}
produced by whatever labeller you build (tools/rl/label.py is the intended home).

Fit: for each served tier t ∈ {strong, cloud} vs weak, a logistic regression on features()
predicting "reward was high" — i.e. the policy learns P(tier is the right call | state). It is
a contextual-bandit starting point, not a full RL loop; the ε-exploration Switchyard already
does (judge.explore_epsilon) is what makes the counterfactual tiers appear in the data.
Pure Python, no numpy, so it runs on the hub with the service venv.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from .rl import FEATURES, POLICY_FILE, features

ROOT = Path(__file__).resolve().parents[1]
RL_DIR = ROOT / "logs" / "rl"
DATASET = ROOT / "logs" / "rl-dataset.jsonl"


def _state_from_trace(t: dict) -> dict:
    msgs = t.get("messages") or []
    users = [m.get("content") or "" for m in msgs if m.get("role") == "user"]
    return {"turn": max(1, len(users)), "first_user_text": users[0] if users else "",
            "summary": " ".join(str(m.get("content") or "")[:400] for m in msgs[-6:]),
            "has_tools": bool(t.get("tools")), "task_type": (t.get("decision") or {}).get("task_type")}


def build_dataset(labels: dict[str, float] | None = None) -> list[dict]:
    rows = []
    for p in sorted(RL_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text())
        except ValueError:
            continue
        st = _state_from_trace(t)
        dec = t.get("decision") or {}
        rows.append({"uuid": t.get("uuid"), "file": p.name, "features": features(st),
                     "decision_route": dec.get("route"), "explore": dec.get("explore"),
                     "served_tier": t.get("served_tier"), "token_count": t.get("token_count"),
                     "reward": (labels or {}).get(t.get("uuid"))})
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    with open(DATASET, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def fit(rows: list[dict], epochs: int = 300, lr: float = 0.1, l2: float = 1e-3) -> dict:
    """One logistic regressor per non-weak tier: P(high reward | features, served that tier)."""
    n = len(FEATURES)
    weights, bias = {}, {}
    for tier in ("strong", "cloud"):
        data = [(r["features"], 1.0 if r["reward"] >= 0.5 else 0.0)
                for r in rows if r.get("reward") is not None and r.get("served_tier") == tier]
        w, b = [0.0] * n, 0.0
        if len(data) >= 10:
            for _ in range(epochs):
                random.shuffle(data)
                for x, y in data:
                    z = sum(a * c for a, c in zip(w, x)) + b
                    p = 1.0 / (1.0 + math.exp(-z))
                    g = p - y
                    w = [wi - lr * (g * xi + l2 * wi) for wi, xi in zip(w, x)]
                    b -= lr * g
        weights[tier], bias[tier] = w, b
    return {"version": "linear-v1", "features": list(FEATURES), "weights": weights, "bias": bias,
            "trained_on": sum(1 for r in rows if r.get("reward") is not None)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", help="JSONL of {uuid, reward}")
    ap.add_argument("--fit", action="store_true", help="fit and write the policy file")
    ap.add_argument("--out", default=str(POLICY_FILE))
    a = ap.parse_args(argv)
    labels = None
    if a.labels:
        labels = {}
        for line in Path(a.labels).read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                labels[d["uuid"]] = float(d["reward"])
    rows = build_dataset(labels)
    tiers = {}
    for r in rows:
        tiers[r["served_tier"]] = tiers.get(r["served_tier"], 0) + 1
    print(f"dataset: {len(rows)} rows → {DATASET}  served_tier={tiers}  labelled={sum(1 for r in rows if r['reward'] is not None)}")
    if a.fit:
        pol = fit(rows)
        Path(a.out).write_text(json.dumps(pol, indent=2))
        print(f"policy → {a.out} (trained_on={pol['trained_on']}); restart the decider with --backend rl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
