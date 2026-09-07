"""Watchdog loop: registry × health → route table → gateway.

Runs as `python -m homed.control.watchdog`. Every INTERVAL seconds it assesses the
registry; a node counts as dead after MISSES consecutive failures. When the derived
mode changes, or the registry file changed (a node joined/left), it regenerates the
route table and restarts the gateway. Dead local lanes are handed to heal.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from ..paths import LOGS, REGISTRY_FILE, ensure_dirs
from ..router import gateway, routes
from ..util.procs import healthy
from . import heal, registry
from .health import Assessment, mode as compute_mode
from .registry import Node, by_pool

INTERVAL = int(os.environ.get("HOMED_WATCHDOG_INTERVAL", "10"))
MISSES = 2
HEAL = os.environ.get("HOMED_HEAL", "1") == "1"


def log(msg: str) -> None:
    ensure_dirs()
    with open(LOGS / "watchdog.log", "a") as f:
        f.write(f"{datetime.now():%F %T} watchdog: {msg}\n")


def run() -> None:
    misses: dict[str, int] = {}
    last_mode = routes.current_mode()
    last_reg_mtime = REGISTRY_FILE.stat().st_mtime if REGISTRY_FILE.exists() else 0
    log(f"started interval={INTERVAL}s heal={'on' if HEAL else 'off'} mode={last_mode}")
    while True:
        time.sleep(INTERVAL)
        if heal.in_progress():
            continue
        nodes = registry.load()
        # debounced health: a node is dead only after MISSES consecutive misses
        a = Assessment()
        for pool, lst in by_pool(nodes).items():
            a.healthy[pool] = []
            for n in lst:
                if healthy(n.health_url):
                    misses[n.name] = 0
                    a.healthy[pool].append(n)
                else:
                    misses[n.name] = misses.get(n.name, 0) + 1
                    if misses[n.name] >= MISSES:
                        a.dead.append(n)
                    else:
                        a.healthy[pool].append(n)   # grace period: still considered live
        cloud = routes.cloud_from_env()
        new_mode = compute_mode(a, cloud.enabled)
        reg_mtime = REGISTRY_FILE.stat().st_mtime if REGISTRY_FILE.exists() else 0
        changed = new_mode != last_mode or reg_mtime != last_reg_mtime
        if changed:
            log(f"change: mode {last_mode} -> {new_mode}, registry_changed={reg_mtime != last_reg_mtime}")
            try:
                routes.write(a, cloud)
                gateway.restart()
                log("gateway restarted")
            except RuntimeError as exc:
                log(f"route generation failed: {exc}")
            last_mode, last_reg_mtime = new_mode, reg_mtime
        dead_local = [n for n in a.dead if n.local]
        if HEAL and dead_local and not heal.in_progress():
            log(f"heal dispatch for {[n.name for n in dead_local]}")
            heal.dispatch()


if __name__ == "__main__":
    run()
