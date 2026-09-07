"""Hardware probe and pool assignment rules (design.md part 1 §3/§4)."""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Hardware:
    kind: str            # "nvidia" | "apple" | "cpu"
    label: str           # human readable, e.g. "NVIDIA GeForce RTX 4090 D · 24564 MiB"
    vram_mib: int = 0    # NVIDIA only
    ram_gib: int = 0


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def probe() -> Hardware:
    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out:
            name, mem = [x.strip() for x in out.splitlines()[0].split(",")[:2]]
            return Hardware("nvidia", f"{name} · {mem} MiB", vram_mib=int(mem))
    if platform.system() == "Darwin":
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).replace("(R)", "").replace("(TM)", "")
        mem = _run(["sysctl", "-n", "hw.memsize"])
        ram = int(mem) // (1024 ** 3) if mem.isdigit() else 0
        kind = "apple" if platform.machine() == "arm64" else "cpu"
        return Hardware(kind, f"{chip or platform.machine()} · {ram}GB RAM · CPU inference", ram_gib=ram)
    return Hardware("cpu", f"{platform.processor() or platform.machine()} · CPU inference")


def choose_preset(hw: Hardware) -> str:
    """Map hardware to a preset name. Presets live in homed/inference/presets/."""
    if hw.kind == "nvidia":
        if hw.vram_mib >= 22000:
            return "qwen3-24gb"        # strong 32B + weak 1.7B slice (measured)
        if hw.vram_mib >= 15000:
            return "qwen3-16gb"        # extrapolated tier
        return "qwen3-12gb"
    return "cpu-1.7b"                  # llama.cpp weak lane (Intel Mac / any CPU)
