"""Pi provider config (~/.pi/agent/models.json) — the only harness integration point."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..paths import GATEWAY_PORT, ROOT

EXTENSIONS_SRC = ROOT / "homed" / "access" / "extensions"

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
    sync_extensions()
    write_settings()
    return path


def write_settings() -> Path:
    """Merge cluster-appropriate defaults into ~/.pi/agent/settings.json.

    Pi auto-compacts when contextTokens > contextWindow - compaction.reserveTokens
    (reserve defaults to 16384). Our lanes declare 5120–8192 token windows, so the
    default would trigger compaction on every turn; scale the reserve down."""
    path = Path.home() / ".pi" / "agent" / "settings.json"
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
    """Copy homed/access/extensions/*.ts into Pi's extension dir. Adding an internal
    system = adding one .ts file there; nothing else changes."""
    dst = Path.home() / ".pi" / "agent" / "extensions"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in EXTENSIONS_SRC.glob("*.ts"):
        shutil.copy2(src, dst / src.name)
        n += 1
    return n
