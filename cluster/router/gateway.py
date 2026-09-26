"""Switchyard gateway process."""
from __future__ import annotations

from ..paths import GATEWAY_PORT, LOGS, ROUTES_FILE, switchyard_bin
from ..util import procs

NAME = "switchyard"
RL_DIR = LOGS / "rl"   # one JSON per request: messages, decision (fork), served_tier — the RL training feed


def url() -> str:
    return f"http://127.0.0.1:{GATEWAY_PORT}/v1"


def restart(host: str = "0.0.0.0") -> bool:
    procs.stop(NAME)
    RL_DIR.mkdir(parents=True, exist_ok=True)
    procs.spawn(NAME, [switchyard_bin(), "serve", "--routing-profiles", str(ROUTES_FILE),
                       "--host", host, "--port", str(GATEWAY_PORT), "--inbound", "both",
                       "--enable-rl-logging", "--rl-log-dir", str(RL_DIR)])
    return procs.wait_http(url() + "/models", tries=24, interval=2)


def healthy() -> bool:
    return procs.healthy(url() + "/models")
