#!/usr/bin/env bash
# Tandem one-command install: probe GPU → pick profile → start the stack.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> checking prerequisites"
command -v docker >/dev/null || { echo "docker is required"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required"; exit 1; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi not found — NVIDIA driver required"; exit 1; }
python3 -c "import yaml" 2>/dev/null || { echo "pyyaml required: pip3 install pyyaml"; exit 1; }

echo "==> autotuning for this GPU"
python3 autotune/autotune.py

echo "==> starting the stack (first run downloads model weights — tens of GB)"
docker compose up -d --build

cat <<'EOF'

==> done. Try it:

  curl http://localhost:8080/healthz

  curl http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"auto","messages":[{"role":"user","content":"把这句话翻译成英文：今天天气不错"}]}' -i

  # watch which lane served it: x-tandem-lane / x-tandem-reason headers
  # cost dashboard: curl http://localhost:8080/admin/stats
EOF
