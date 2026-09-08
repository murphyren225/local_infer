"""Pi harness wiring (client side). Writes three things under ~/.pi/agent/:

  models.json   provider "home" → the cluster gateway; one entry per route
  settings.json defaults (provider/model) and compaction sized for our context windows
  extensions/   our tool extensions, copied from client/pi/extensions/

Input is only the gateway URL. Nothing here knows how the cluster is built.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTENSIONS_SRC = ROOT / "client" / "pi" / "extensions"
PI_HOME = Path.home() / ".pi" / "agent"

ROUTES = ["auto", "small", "large", "cloud"]
CONTEXT = {"auto": 5120, "small": 8192, "large": 5120, "cloud": 32768, "long": 65536}


def write_models(gateway_url: str, routes: list[str] | None = None) -> Path:
    routes = routes or ROUTES
    models = [
        {"id": r, "name": f"Cluster {r}", "input": ["text"],
         "contextWindow": CONTEXT.get(r, 5120), "maxTokens": 1024,
         "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}
        for r in routes
    ]
    cfg = {"providers": {"home": {"baseUrl": gateway_url.rstrip("/"),
                                  "api": "openai-completions", "apiKey": "home",
                                  "models": models}}}
    path = PI_HOME / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    return path


def write_settings() -> Path:
    """Pi auto-compacts when contextTokens > contextWindow - reserveTokens (reserve
    defaults to 16384); our lanes declare 5–8K windows, so scale the reserve down."""
    path = PI_HOME / "settings.json"
    try:
        cfg = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        cfg = {}
    cfg.setdefault("defaultProvider", "home")
    cfg.setdefault("defaultModel", "auto")
    comp = cfg.setdefault("compaction", {})
    comp.setdefault("reserveTokens", 1024)
    comp.setdefault("keepRecentTokens", 2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))
    return path


def sync_extensions() -> int:
    dst = PI_HOME / "extensions"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in EXTENSIONS_SRC.glob("*.ts"):
        shutil.copy2(src, dst / src.name)
        n += 1
    return n


def setup(gateway_url: str) -> dict:
    return {"models": str(write_models(gateway_url)),
            "settings": str(write_settings()),
            "extensions": sync_extensions()}
