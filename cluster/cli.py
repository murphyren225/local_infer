"""cluster CLI — the inference side. Orchestrates the layers; contains no policy.

  cluster init [--preset P]          this machine becomes the Hub (+ its local lanes)
  cluster join HUB_URL --token T     this machine joins an existing Hub (LAN)
  cluster link-gpu "SSH_TARGET"      Hub adopts a remote GPU node over an SSH tunnel
  cluster status | stop | regen

The client side (Pi, personal web UI) is a separate system: see client/ and bin/client.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.request

from . import paths
from .control import health, registry
from .control.registry import Node
from .node import engine, presets, probe
from .router import gateway, routes
from .util import procs

CONSOLE = paths.ROOT / "cluster" / "access" / "console" / "console.py"


def say(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---------------------------------------------------------------- shared steps

def start_local_lanes(preset_name: str) -> list[presets.Lane]:
    lanes = presets.load(preset_name)
    started = []
    # strong first: a cold 32B start needs the whole card (design.md part 3 §4.2)
    for lane in sorted(lanes, key=lambda l: 0 if l.pool == "strong" else 1):
        say(f"starting {lane.pool} lane: {lane.name} ({lane.engine})")
        if engine.start(lane, wait_tries=240 if lane.engine == "vllm" else 60):
            ok(f"{lane.pool} lane healthy on :{lane.port}")
            started.append(lane)
        else:
            fail(f"{lane.pool} lane failed (logs/engine-{lane.pool}.log) — continuing without it")
    (paths.STATE / "preset").write_text(preset_name)
    return started


def register_local(lanes: list[presets.Lane], hw: probe.Hardware) -> None:
    host = socket.gethostname().split(".")[0]
    for lane in lanes:
        registry.register(Node(f"{host}-{lane.pool}", lane.pool, lane.name, lane.base_url,
                               hw=hw.label, engine=lane.engine, local=True))


def regen() -> str:
    a = health.assess(registry.load())
    m = routes.write(a)
    if not gateway.restart():
        raise RuntimeError("gateway failed to start (logs/switchyard.log)")
    return m


def start_console() -> None:
    url = f"http://127.0.0.1:{paths.CONSOLE_PORT}/api/status"
    if procs.healthy(url):
        ok("console already running")
        return
    procs.spawn("console", [paths.python_for_services(), str(CONSOLE)])
    ok("console started" if procs.wait_http(url, 10, 1) else "console starting (check logs/console.log)")


def start_watchdog(heal: bool) -> None:
    if procs.alive("watchdog"):
        ok("watchdog already running")
        return
    procs.spawn("watchdog", [sys.executable, "-m", "cluster.control.watchdog"],
                env={"HOMED_HEAL": "1" if heal else "0"})
    ok("watchdog started")


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    paths.ensure_dirs()
    hw = probe.probe()
    preset_name = args.preset or probe.choose_preset(hw)
    print(f"== init: {hw.label} → preset {preset_name}")
    lanes = start_local_lanes(preset_name)
    register_local(lanes, hw)
    token = registry.issue_token()
    try:
        m = regen()
        ok(f"gateway up, mode={m}")
    except RuntimeError as exc:
        fail(f"{exc}; gateway waits for a lane (join/link-gpu)")
    start_console()
    start_watchdog(heal=bool(lanes))
    print("\n== Hub ready")
    say(f"console   http://localhost:{paths.CONSOLE_PORT}")
    say(f"api       http://<this-host>:{paths.GATEWAY_PORT}/v1   model: auto|small|large|cloud")
    say(f"join      cluster join http://<this-host>:{paths.CONSOLE_PORT} --token {token}")
    say(f"clients   bin/client setup --hub http://<this-host>:{paths.GATEWAY_PORT}   (on each user's machine)")
    return 0


def _lan_ip(toward_host: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward_host, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def cmd_join(args) -> int:
    paths.ensure_dirs()
    hw = probe.probe()
    preset_name = args.preset or probe.choose_preset(hw)
    print(f"== join {args.hub}: {hw.label} → preset {preset_name}")
    lanes = start_local_lanes(preset_name)
    if not lanes:
        fail("no lane started; nothing to register")
        return 1
    host = urllib.request.urlparse(args.hub).hostname or "127.0.0.1"
    my_ip = _lan_ip(host)
    me = socket.gethostname().split(".")[0]
    for lane in lanes:
        body = {"token": args.token, "name": f"{me}-{lane.pool}", "pool": lane.pool,
                "model": lane.name, "base_url": f"http://{my_ip}:{lane.port}/v1",
                "hw": hw.label, "engine": lane.engine}
        req = urllib.request.Request(args.hub.rstrip("/") + "/api/register",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ok(f"registered {body['name']} → {json.load(r)}")
        except (urllib.error.URLError, OSError) as exc:
            fail(f"register {body['name']}: {exc}")
            return 1
    say("Hub's watchdog picks the registry change up within one interval (10s)")
    return 0


def cmd_link_gpu(args) -> int:
    """Hub-initiated adoption of a remote GPU node through an SSH tunnel."""
    paths.ensure_dirs()
    fwd = ["-L", f"{args.port}:127.0.0.1:{args.port}"]
    local_weak = any(n.pool == "weak" and n.local for n in registry.load().values())
    if not local_weak:
        fwd += ["-L", "8002:127.0.0.1:8002"]
        say("no local weak lane → tunnelling the remote weak lane too")
    ssh_opts = ["-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=no"]
    loop = f"while true; do ssh {' '.join(ssh_opts)} -N {' '.join(fwd)} {args.target}; sleep 5; done"
    procs.stop("tunnel")
    procs.spawn("tunnel", ["bash", "-c", loop])
    if not procs.wait_http(f"http://127.0.0.1:{args.port}/health", 15, 2):
        procs.stop("tunnel")
        fail("remote strong lane not reachable through the tunnel (is the node up?)")
        return 1
    ok("tunnel up")
    hw = subprocess.run(["ssh", *ssh_opts, "-o", "ConnectTimeout=10", *args.target.split(),
                         "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader"],
                        capture_output=True, text=True).stdout.strip().replace(", ", " · ") or args.target
    registry.register(Node(args.name, "strong", args.model, f"http://127.0.0.1:{args.port}/v1",
                           hw=hw, engine="remote", local=False))
    if not local_weak:
        registry.register(Node(args.name + "-weak", "weak", args.weak_model, "http://127.0.0.1:8002/v1",
                               hw=hw, engine="remote", local=False))
    m = regen()
    ok(f"registered {args.name}, mode={m}")
    return 0


def cmd_status(args) -> int:
    nodes = registry.load()
    a = health.assess(nodes)
    for n in nodes.values():
        live = any(x.name == n.name for x in a.healthy.get(n.pool, []))
        (ok if live else fail)(f"{n.pool:<6} {n.name:<18} {n.model:<18} {'local' if n.local else 'remote'}  {n.hw}")
    (ok if gateway.healthy() else fail)(f"gateway :{paths.GATEWAY_PORT}")
    (ok if procs.healthy(f'http://127.0.0.1:{paths.CONSOLE_PORT}/api/status') else fail)(f"console :{paths.CONSOLE_PORT}")
    (ok if procs.alive('watchdog') else fail)("watchdog")
    say(f"mode: {routes.current_mode()}")
    return 0


def cmd_stop(args) -> int:
    for name in ("watchdog", "console", gateway.NAME, "tunnel", "engine-strong", "engine-weak"):
        if procs.stop(name):
            say(f"stopped {name}")
    return 0


def cmd_regen(args) -> int:
    m = regen()
    ok(f"routes regenerated, mode={m}")
    return 0


def main(argv=None) -> int:
    os.chdir(paths.ROOT)
    p = argparse.ArgumentParser(prog="cluster", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--preset"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("join"); s.add_argument("hub"); s.add_argument("--token", required=True); s.add_argument("--preset"); s.set_defaults(fn=cmd_join)
    s = sub.add_parser("link-gpu"); s.add_argument("target"); s.add_argument("--name", default="gpu-remote")
    s.add_argument("--model", default="qwen3-32b-awq"); s.add_argument("--weak-model", default="qwen3-1.7b-fp8")
    s.add_argument("--port", type=int, default=8001); s.set_defaults(fn=cmd_link_gpu)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    sub.add_parser("regen").set_defaults(fn=cmd_regen)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        fail(str(exc))
        return 1
