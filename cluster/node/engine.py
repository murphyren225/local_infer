"""Engine processes: start/stop one lane with vLLM or llama.cpp."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..util import procs
from .presets import Lane


def _llama_server() -> str:
    for cand in (
        Path.home() / ".homed" / "pkg" / "src" / "build" / "bin" / "llama-server",
        Path.home() / ".homed" / "bin" / "llama-server",
    ):
        if cand.exists():
            return str(cand)
    found = shutil.which("llama-server")
    if not found:
        raise RuntimeError("llama-server not found (build llama.cpp or install it)")
    return found


def _vllm() -> str:
    cand = Path(os.environ.get("HOMED_VENV", Path.home() / ".homed" / "venv")) / "bin" / "vllm"
    if cand.exists():
        return str(cand)
    found = shutil.which("vllm")
    if not found:
        raise RuntimeError("vllm not found (bin/install.sh installs it into ~/.homed/venv)")
    return found


def command(lane: Lane) -> list[str]:
    if lane.engine == "vllm":
        return [_vllm(), "serve", lane.model_path, "--served-model-name", lane.name,
                "--port", str(lane.port), *lane.args]
    if lane.engine == "llama.cpp":
        return [_llama_server(), "-m", lane.model_path, "-a", lane.name,
                "--host", "127.0.0.1", "--port", str(lane.port), *lane.args]
    raise ValueError(f"unknown engine {lane.engine}")


def proc_name(lane: Lane) -> str:
    return f"engine-{lane.pool}"


def start(lane: Lane, wait_tries: int = 240) -> bool:
    """Start a lane unless it is already healthy. Returns health after wait."""
    if procs.healthy(lane.health_url):
        return True
    if lane.engine == "llama.cpp" and not Path(lane.model_path).exists():
        return False
    env = {"PYTORCH_CUDA_ALLOC_CONF": os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")}
    if lane.engine == "vllm":
        # vLLM's JIT (flashinfer) shells out to `ninja`, which lives in the venv's bin
        env["PATH"] = str(Path(_vllm()).parent) + os.pathsep + os.environ.get("PATH", "")
    procs.spawn(proc_name(lane), command(lane), env=env)
    return procs.wait_http(lane.health_url, wait_tries)


def stop(lane: Lane) -> bool:
    return procs.stop(proc_name(lane))
