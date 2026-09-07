"""Switchyard gateway process."""
from __future__ import annotations

from ..paths import GATEWAY_PORT, ROUTES_FILE, switchyard_bin
from ..util import procs

NAME = "switchyard"


def url() -> str:
    return f"http://127.0.0.1:{GATEWAY_PORT}/v1"


def restart(host: str = "0.0.0.0") -> bool:
    procs.stop(NAME)
    procs.spawn(NAME, [switchyard_bin(), "serve", "--routing-profiles", str(ROUTES_FILE),
                       "--host", host, "--port", str(GATEWAY_PORT), "--inbound", "both"])
    return procs.wait_http(url() + "/models", tries=24, interval=2)


def healthy() -> bool:
    return procs.healthy(url() + "/models")
