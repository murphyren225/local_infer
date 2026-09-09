#!/usr/bin/env bash
# Client side (one per person): install Pi, then wire it to the cluster gateway.
#
#   bin/install-client.sh http://<hub>:4000
#
# Needs Node >= 22 (nodejs.org or brew/apt). Python 3 is only used for the personal
# web front-end (bin/client web) and is present on macOS / most Linux by default.
set -euo pipefail
HUB="${1:-}"
[ -n "$HUB" ] || { echo "usage: $0 http://<hub>:4000"; exit 1; }
say() { printf '\n== %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }

say "Pi CLI"
if command -v pi >/dev/null; then
  ok "pi $(pi --version 2>/dev/null | head -1) already installed"
elif command -v npm >/dev/null; then
  # global prefix is often root-owned (Homebrew/pkg installs); fall back to a per-user prefix, no sudo
  if [ -w "$(npm config get prefix)/lib/node_modules" ] 2>/dev/null; then PFX=""; else PFX="--prefix $HOME/.cluster-client/npm"; fi
  npm install -g --ignore-scripts $PFX @earendil-works/pi-coding-agent >/dev/null 2>&1 && ok "pi installed${PFX:+ (user prefix ~/.cluster-client/npm)}" \
    || { echo "  ✗ npm install failed; install Node >= 22 and retry"; exit 1; }
else
  echo "  ✗ npm not found: install Node >= 22 (https://nodejs.org) and rerun"; exit 1
fi

say "wire Pi to $HUB"
"$(dirname "$0")/client" setup --hub "$HUB"
"$(dirname "$0")/client" up

say "done"
echo "  bin/client pi                        # terminal agent, default home/auto"
echo "  http://127.0.0.1:7000                # personal web UI (Chat = inference, Agent = pi on this machine)"
echo "  bin/client ask \"…\" --lane auto   bin/client batch prompts.txt   # inference jobs without the harness"
