#!/usr/bin/env python3
"""Write the PAIR user override that puts our engine into PAIR's LM slot — no change to PAIR itself.

PAIR deep-merges <config>/engines/<engine>.json onto its bundled manifest at engine-manager start. This file
carries the whole engine definition (detect, process runtime, probes, model listing), so an unmodified PAIR
launches, health-checks and advertises SGLang or llama-server as its "lmstudio" engine.

  engine_override.py sglang       --model /path/Qwen3-1.7B  --name qwen3-1.7b [--draft ngram] [--out FILE]
  engine_override.py llama-server --model /path/model.gguf  --name qwen3-1.7b [--draft ngram] [--port 11235]

{install_dir} is <config>/engine-bin/lmstudio: put (or symlink) `sglang-venv/` or `llama-server` there.
"""
import argparse
import json
import os
import sys
from pathlib import Path

CONFIG = {"darwin": Path.home() / "Library/Application Support/Nvidia Corporation/Personal AI Router",
          "linux": Path.home() / ".config/Nvidia Corporation/Personal AI Router"}


def build(engine: str, model: str, name: str, draft: str, port: int, ctx: int, mem: float) -> dict:
    if engine == "sglang":
        detect = "{install_dir}/sglang-venv/bin/python"
        args = ["-m", "sglang.launch_server", "--model-path", model, "--served-model-name", name,
                "--host", "{host}", "--port", "{port}", "--context-length", str(ctx),
                "--mem-fraction-static", str(mem), "--reasoning-parser", "qwen3"]
        if draft == "ngram":
            args += ["--speculative-algorithm", "NGRAM", "--speculative-num-draft-tokens", "8"]
        ready = 1200
    else:
        detect = "{install_dir}/llama-server"
        args = ["-m", model, "--alias", name, "--host", "{host}", "--port", "{port}", "-c", str(ctx), "--jinja"]
        if draft == "ngram":
            args += ["--spec-type", "ngram-simple", "--spec-draft-n-max", "8"]
        ready = 300
    # "match": None clears the bundled loaded_instances filter, which only LM Studio's native API can satisfy
    models = {"http": {"method": "GET", "path": "/v1/models"}, "result": {"array": "data", "field": "id", "match": None}}
    return {"engine": "lmstudio", "display_name": "SGLang" if engine == "sglang" else "llama.cpp",
            "detect": [detect],
            "runtime": {"mode": "process", "port": port, "bind": "127.0.0.1", "bin": detect, "args": args,
                        "ready": {"http": "http://127.0.0.1:{port}/v1/models", "status": 200, "timeout_s": ready},
                        "health": {"http": "http://127.0.0.1:{port}/v1/models", "status": 200, "interval_s": 10},
                        "stop": {"signal": "term", "grace_s": 15}},
            "actions": {"list_models": models, "loaded_models": models}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("engine", choices=["sglang", "llama-server"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--name", default="qwen3-1.7b")
    ap.add_argument("--draft", default="ngram", choices=["ngram", "none"])
    ap.add_argument("--port", type=int)
    ap.add_argument("--context-length", type=int, default=8192)
    ap.add_argument("--mem-fraction", type=float, default=0.5)
    ap.add_argument("--out")
    a = ap.parse_args()
    port = a.port or (30000 if a.engine == "sglang" else 11235)
    out = Path(a.out) if a.out else CONFIG["darwin" if sys.platform == "darwin" else "linux"] / "engines" / "lmstudio.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(a.engine, a.model, a.name, a.draft, port, a.context_length, a.mem_fraction), indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
