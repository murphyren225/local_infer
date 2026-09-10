"""client CLI — the personal side, one per person.

  client setup --hub URL          wire Pi to the model side (models, settings, extensions); remembers URL
  client up   [--hub URL] [--port 7000]   start the local service in the background (web UI + proxy + agent endpoint)
  client down                     stop it
  client status                   what is configured and running
  client pi   [pi args…]          open the Pi harness (its provider points at the local service, which is started if needed)
  client ask  [--lane auto] "prompt"      one inference call, no harness
  client batch FILE [--lane auto] [-c 4] [-o out.jsonl]   many prompts concurrently (jsonl {"prompt":…} or one per line)
  client web  [--hub URL] [--port 7000]   the local service in the foreground

--hub is remembered in ~/.cluster-client/config.json after setup/up, so later commands can omit it.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import gateway
from .pi import setup as pi_setup

HOME = Path.home() / ".cluster-client"
CONFIG = HOME / "config.json"
PID = HOME / "web.pid"
LOG = HOME / "web.log"


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def _save(**kv) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = _config()
    cfg.update(kv)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def _hub(args) -> str:
    hub = getattr(args, "hub", None) or _config().get("hub")
    if not hub:
        sys.exit("  no hub configured: run `client setup --hub http://<hub>:4000` first")
    return hub.rstrip("/").removesuffix("/v1")


def _web_pid() -> int | None:
    try:
        pid = int(PID.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _local(port: int | None = None) -> str:
    return f"http://127.0.0.1:{port or _config().get('port', 7000)}"


def cmd_setup(args) -> int:
    hub = _hub(args)
    _save(hub=hub)
    out = pi_setup.setup(_local() + "/v1")
    print(f"  ✓ Pi provider 'home' → {_local()}/v1 (local service) → {hub}   ({out['models']})")
    print("  ✓ settings: default provider/model = home/auto, compaction sized for small windows")
    print(f"  ✓ {out['extensions']} extension(s) synced to ~/.pi/agent/extensions/")
    if gateway.pi_bin():
        print("  → client pi        (terminal agent)      client up   (local web UI)")
    else:
        print("  → Pi not installed: npm install -g --ignore-scripts @earendil-works/pi-coding-agent")
    return 0


def _ensure_up(hub: str, port: int, quiet: bool = False) -> int:
    _save(hub=hub, port=port)
    if (pid := _web_pid()):
        if not quiet:
            print(f"  already running (pid {pid}) at {_local(port)}")
        return pid
    HOME.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CLIENT_GATEWAY": hub, "CLIENT_PORT": str(port)}
    with open(LOG, "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "client.serve"], cwd=str(Path(__file__).resolve().parents[1]),
                                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    PID.write_text(str(proc.pid))
    for _ in range(20):
        if gateway.healthy(f"{_local(port)}/api/config"):
            break
        time.sleep(0.25)
    if not quiet:
        print(f"  ✓ local service {_local(port)}  →  {hub}   (pid {proc.pid}, log {LOG})")
    return proc.pid


def cmd_up(args) -> int:
    _ensure_up(_hub(args), args.port)
    return 0


def cmd_down(args) -> int:
    pid = _web_pid()
    if not pid:
        print("  not running")
        return 0
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    PID.unlink(missing_ok=True)
    print(f"  stopped (pid {pid})")
    return 0


def cmd_web(args) -> int:
    from . import serve
    hub = _hub(args)
    _save(hub=hub, port=args.port)
    serve.GATEWAY = hub
    print(f"  local service http://127.0.0.1:{args.port}  →  {hub}")
    serve.run(args.port)
    return 0


def cmd_pi(args) -> int:
    pi = gateway.pi_bin()
    if not pi:
        sys.exit("  pi not installed: bin/install-client.sh http://<hub>:4000")
    _ensure_up(_hub(args), _config().get("port", 7000), quiet=True)  # Pi's provider points at the local service
    os.execv(pi, ["pi", *args.rest])


def cmd_ask(args) -> int:
    try:
        r = gateway.chat(_hub(args), args.lane, args.prompt, max_tokens=args.max_tokens, session=uuid.uuid4().hex[:16])
    except (OSError, RuntimeError) as exc:
        sys.exit(f"  model side unreachable or refused: {exc}")
    print(r["content"])
    print(f"\n  [{r['model']} · {r['latency_ms'] / 1000:.1f}s · {r['tokens']} tok]", file=sys.stderr)
    return 0


def cmd_batch(args) -> int:
    hub = _hub(args)
    prompts = gateway.read_prompts(Path(args.file))
    out = open(args.out, "w") if args.out else sys.stdout
    t0 = time.monotonic()

    def one(p):
        try:
            r = gateway.chat(hub, args.lane, p, max_tokens=args.max_tokens, session=uuid.uuid4().hex[:16])
            return {"prompt": p, "answer": r["content"], "model": r["model"], "latency_ms": r["latency_ms"]}
        except Exception as exc:  # keep the batch going; the row records the failure
            return {"prompt": p, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for row in ex.map(one, prompts):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
    print(f"  {len(prompts)} prompts, {args.concurrency} concurrent, {time.monotonic() - t0:.1f}s", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    cfg = _config()
    print(f"  hub: {cfg.get('hub') or 'not configured'}")
    pid = _web_pid()
    print(f"  local service: {'running pid %s at http://127.0.0.1:%s' % (pid, cfg.get('port', 7000)) if pid else 'stopped'}")
    p = Path.home() / ".pi" / "agent" / "models.json"
    if p.exists():
        home = json.loads(p.read_text()).get("providers", {}).get("home", {})
        print(f"  Pi provider home → {home.get('baseUrl')}  models: {[m['id'] for m in home.get('models', [])]}")
    else:
        print("  Pi not configured (run: client setup --hub …)")
    ext = Path.home() / ".pi" / "agent" / "extensions"
    print(f"  extensions: {sorted(x.name for x in ext.glob('*.ts')) if ext.exists() else []}")
    print(f"  pi binary: {gateway.pi_bin() or 'not installed'}")
    if cfg.get("hub"):
        try:
            print(f"  hub routes: {gateway.models(cfg['hub'])}")
        except Exception as exc:
            print(f"  hub unreachable: {exc}")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["pi"]:  # everything after `pi` belongs to Pi, including its own --flags
        return cmd_pi(argparse.Namespace(hub=None, rest=argv[1:]))
    p = argparse.ArgumentParser(prog="client", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, hub=True, port=False):
        s = sub.add_parser(name)
        if hub:
            s.add_argument("--hub")
        if port:
            s.add_argument("--port", type=int, default=7000)
        s.set_defaults(fn=fn)
        return s

    add("setup", cmd_setup)
    add("up", cmd_up, port=True)
    add("down", cmd_down, hub=False)
    add("web", cmd_web, port=True)
    add("status", cmd_status, hub=False)
    sub.add_parser("pi", help="pi [args…] — handled before argparse")
    s = add("ask", cmd_ask); s.add_argument("prompt"); s.add_argument("--lane", default="auto"); s.add_argument("--max-tokens", type=int, default=1024)
    s = add("batch", cmd_batch); s.add_argument("file"); s.add_argument("--lane", default="auto")
    s.add_argument("-c", "--concurrency", type=int, default=4); s.add_argument("-o", "--out"); s.add_argument("--max-tokens", type=int, default=512)
    args = p.parse_args(argv)
    return args.fn(args)
