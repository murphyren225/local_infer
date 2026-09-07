"""Pi provider config (~/.pi/agent/models.json) — the only harness integration point."""
from __future__ import annotations

import json
from pathlib import Path

from ..paths import GATEWAY_PORT

CONTEXT = {"auto": 5120, "small": 8192, "large": 5120, "cloud": 32768, "long": 65536}


def write(routes: list[str], host: str = "127.0.0.1") -> Path:
    models = [
        {"id": r, "name": f"Cluster {r}", "input": ["text"],
         "contextWindow": CONTEXT.get(r, 5120), "maxTokens": 1024,
         "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}
        for r in routes
    ]
    cfg = {"providers": {"home": {"baseUrl": f"http://{host}:{GATEWAY_PORT}/v1",
                                  "api": "openai-completions", "apiKey": "home",
                                  "models": models}}}
    path = Path.home() / ".pi" / "agent" / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return path
