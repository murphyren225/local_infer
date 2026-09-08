"""client CLI — the Pi side, one per person.

  client setup --hub http://<hub>:4000     wire Pi to the cluster gateway (models, settings, extensions)
  client web   --hub http://<hub>:4000     run the personal web front-end on localhost:7000
  client status                            show what is configured
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from .pi import setup as pi_setup


def cmd_setup(args) -> int:
    gw = args.hub.rstrip("/")
    if not gw.endswith("/v1"):
        gw += "/v1"
    out = pi_setup.setup(gw)
    print(f"  ✓ Pi provider 'home' → {gw}   ({out['models']})")
    print(f"  ✓ settings: default provider/model = home/auto, compaction sized for small windows")
    print(f"  ✓ {out['extensions']} extension(s) synced to ~/.pi/agent/extensions/")
    if shutil.which("pi"):
        print("  → run: pi          (uses home/auto by default)")
    else:
        print("  → Pi not installed: npm install -g --ignore-scripts @earendil-works/pi-coding-agent")
    return 0


def cmd_web(args) -> int:
    from . import serve
    os.environ["CLIENT_GATEWAY"] = args.hub.rstrip("/").removesuffix("/v1")
    print(f"  personal client at http://127.0.0.1:{args.port}  →  gateway {os.environ['CLIENT_GATEWAY']}")
    serve.GATEWAY = os.environ["CLIENT_GATEWAY"]
    serve.run(args.port)
    return 0


def cmd_status(args) -> int:
    p = Path.home() / ".pi" / "agent" / "models.json"
    if p.exists():
        cfg = json.loads(p.read_text())
        home = cfg.get("providers", {}).get("home", {})
        print(f"  Pi provider home → {home.get('baseUrl')}  models: {[m['id'] for m in home.get('models', [])]}")
    else:
        print("  Pi not configured (run: client setup --hub …)")
    ext = Path.home() / ".pi" / "agent" / "extensions"
    print(f"  extensions: {sorted(x.name for x in ext.glob('*.ts')) if ext.exists() else []}")
    print(f"  pi binary: {shutil.which('pi') or 'not installed'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="client", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup"); s.add_argument("--hub", required=True); s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("web"); s.add_argument("--hub", required=True); s.add_argument("--port", type=int, default=7000); s.set_defaults(fn=cmd_web)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    return args.fn(args)
