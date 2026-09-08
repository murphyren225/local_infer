"""Heal: relaunch dead local lanes.

Staged restart rule (measured on 24GB): a 32B vLLM cold start peaks ~20.5GiB and
needs the whole card, so if the strong lane died while a weak vLLM lane still holds
memory, the weak lane is stopped first, strong is started, then weak is restarted.
Remote nodes are never healed here (their host owns them).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime

from ..paths import LOGS, STATE, ensure_dirs
from ..node import engine, presets
from ..util.procs import healthy

LOCK = LOGS / "heal.lock"


def in_progress() -> bool:
    return LOCK.exists()


def log(msg: str) -> None:
    ensure_dirs()
    with open(LOGS / "watchdog.log", "a") as f:
        f.write(f"{datetime.now():%F %T} heal: {msg}\n")


def dispatch() -> None:
    """Spawn heal as a separate process so the watchdog loop keeps polling."""
    ensure_dirs()
    LOCK.touch()
    subprocess.Popen([sys.executable, "-m", "cluster.control.heal"], start_new_session=True,
                     stdout=open(LOGS / "heal.log", "ab"), stderr=subprocess.STDOUT)


def run() -> None:
    try:
        preset_file = STATE / "preset"
        if not preset_file.exists():
            log("no local preset recorded; nothing to heal")
            return
        lanes = presets.load(preset_file.read_text().strip())
        strong = next((l for l in lanes if l.pool == "strong"), None)
        weak = next((l for l in lanes if l.pool == "weak"), None)
        if strong and not healthy(strong.health_url):
            if weak and weak.engine == "vllm" and healthy(weak.health_url):
                log("strong dead: staged restart, evicting weak vLLM lane to free the card")
                engine.stop(weak)
            log("starting strong lane")
            if not engine.start(strong):
                log("strong lane failed to start; see logs/engine-strong.log")
        if weak and not healthy(weak.health_url):
            log("starting weak lane")
            engine.start(weak, wait_tries=60)
        log("done")
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
