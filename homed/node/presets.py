"""Preset files: one per model family × hardware tier.

Format is shell-style KEY=VALUE (kept compatible with the original .env files) with
lane blocks LARGE_* and SMALL_*. A lane declares which pool it serves, which engine
runs it, the model path, the public model name, port and engine arguments.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import MODELS_DIR, PRESETS


@dataclass
class Lane:
    pool: str            # "strong" | "weak"
    engine: str          # "vllm" | "llama.cpp"
    model_path: str
    name: str            # served model name (public)
    port: int
    args: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/health"


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        # ${VAR:-default} style
        if val.startswith("${") and ":-" in val and val.endswith("}"):
            var, default = val[2:-1].split(":-", 1)
            val = os.environ.get(var, default)
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def load(name: str) -> list[Lane]:
    path = PRESETS / f"{name}.env"
    if not path.exists():
        raise FileNotFoundError(f"preset not found: {path}")
    kv = _parse_env(path)
    lanes: list[Lane] = []
    for prefix, pool, default_port in (("LARGE", "strong", 8001), ("SMALL", "weak", 8002)):
        model = kv.get(f"{prefix}_MODEL_PATH") or kv.get(f"{prefix}_MODEL")
        if not model:
            continue
        model = model.replace("~", str(Path.home())).replace("$MODELS", str(MODELS_DIR))
        lanes.append(
            Lane(
                pool=pool,
                engine=kv.get(f"{prefix}_ENGINE", "vllm"),
                model_path=model,
                name=kv[f"{prefix}_NAME"],
                port=int(kv.get(f"{prefix}_PORT", default_port)),
                args=shlex.split(kv.get(f"{prefix}_ARGS", "")),
            )
        )
    if not lanes:
        raise ValueError(f"preset {name} declares no lanes")
    return lanes
