#!/usr/bin/env bash
# Model side, one command on a blank device: install everything, download models, start serving.
#
#   bin/install.sh                # install + download + `cluster init` (this device becomes the Hub)
#   bin/install.sh --join URL --token T   # install + download + join an existing Hub
#   bin/install.sh --no-start     # install + download only
#   bin/install.sh --no-model     # skip model downloads
#   bin/install.sh --hf           # download from Hugging Face instead of ModelScope
#
# What it does, by layer (docs/design.md):
#   调度层  Python >= 3.12 venv at ~/.homed/venv with nemo-switchyard + console deps
#   资源层  NVIDIA box: vLLM into the same venv + Qwen3-32B-AWQ / Qwen3-1.7B-FP8 weights
#           CPU/Mac box: llama.cpp binary (download, or build when the OS is too old) + 1.7B GGUF
# Personal machines run bin/install-client.sh instead.
set -euo pipefail
START=init; MODEL=1; SRC=modelscope; JOIN_URL=""; JOIN_TOKEN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-start) START="" ;;
    --no-model) MODEL=0 ;;
    --hf) SRC=hf ;;
    --join) START=join; JOIN_URL="$2"; shift ;;
    --token) JOIN_TOKEN="$2"; shift ;;
    *) echo "unknown flag $1"; exit 1 ;;
  esac; shift
done
HOMED="$HOME/.homed"
VENV="$HOMED/venv"
MODELS="$HOMED/models"
mkdir -p "$HOMED" "$HOMED/bin" "$HOMED/pkg"
# AutoDL and similar: the system disk is small, weights go to the data disk.
if [ -d /root/autodl-tmp ] && [ ! -e "$MODELS" ]; then mkdir -p /root/autodl-tmp/models && ln -s /root/autodl-tmp/models "$MODELS"; fi
mkdir -p "$MODELS"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf '\n== %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }

# ---- 调度层: venv + switchyard + console deps ----------------------------
say "Python venv for services (needs Python >= 3.12)"
PY=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
  echo "  - no Python >= 3.12 on this machine; installing one with uv (user-local, no sudo)"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
  export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.12 >/dev/null 2>&1 && PY=$(uv python find 3.12) || { echo "  ✗ could not install Python 3.12"; exit 1; }
fi
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q nemo-switchyard fastapi uvicorn httpx python-multipart pyyaml
ok "switchyard $("$VENV/bin/pip" show nemo-switchyard | awk '/^Version/{print $2}') in $VENV"

# ---- 资源层 ---------------------------------------------------------------
if command -v nvidia-smi >/dev/null; then
  say "NVIDIA node: vLLM into $VENV"
  if "$VENV/bin/python" -c 'import vllm' 2>/dev/null; then
    ok "vllm $("$VENV/bin/pip" show vllm | awk '/^Version/{print $2}') already installed"
  else
    "$VENV/bin/pip" install -q vllm && ok "vllm $("$VENV/bin/pip" show vllm | awk '/^Version/{print $2}')"
  fi
  if [ "$MODEL" = 1 ]; then
    say "models: Qwen3-32B-AWQ + Qwen3-1.7B-FP8 → $MODELS ($SRC, resumable)"
    for M in Qwen3-32B-AWQ Qwen3-1.7B-FP8; do
      if [ -f "$MODELS/$M/config.json" ] && ls "$MODELS/$M"/*.safetensors >/dev/null 2>&1; then ok "$M present"; continue; fi
      if [ "$SRC" = hf ]; then
        "$VENV/bin/pip" install -q huggingface_hub
        "$VENV/bin/python" -c "from huggingface_hub import snapshot_download as d; d('Qwen/$M', local_dir='$MODELS/$M')"
      else
        "$VENV/bin/pip" install -q modelscope
        "$VENV/bin/modelscope" download --model "Qwen/$M" --local_dir "$MODELS/$M" >/dev/null
      fi
      ok "$M downloaded"
    done
  fi
else
  say "CPU/Mac node: llama.cpp"
  LLAMA="$HOMED/pkg/src/build/bin/llama-server"
  if [ -x "$LLAMA" ] && "$LLAMA" --version >/dev/null 2>&1; then
    ok "llama-server (built) present"
  elif command -v llama-server >/dev/null && llama-server --version >/dev/null 2>&1; then
    ok "llama-server on PATH"
  else
    TAG=$(curl -s --max-time 20 "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5" \
      | python3 -c 'import json,sys; rs=[r for r in json.load(sys.stdin) if any("macos" in a["name"] or "ubuntu" in a["name"] for a in r["assets"])]; print(rs[0]["tag_name"] if rs else "")' 2>/dev/null || true)
    ARCH=$(uname -m); OS=$(uname -s)
    ASSET=""
    if [ "$OS" = Darwin ]; then [ "$ARCH" = arm64 ] && ASSET="macos-arm64" || ASSET="macos-x64"; else ASSET="ubuntu-x64"; fi
    if [ -n "$TAG" ] && curl -sL --max-time 300 -o "$HOMED/pkg/llama.tar.gz" \
        "https://github.com/ggml-org/llama.cpp/releases/download/$TAG/llama-$TAG-bin-$ASSET.tar.gz" \
        && tar xzf "$HOMED/pkg/llama.tar.gz" -C "$HOMED/pkg" 2>/dev/null \
        && BIN=$(find "$HOMED/pkg" -name llama-server -path "*$TAG*" | head -1) && [ -n "$BIN" ] \
        && "$BIN" --version >/dev/null 2>&1; then
      ln -sf "$BIN" "$HOMED/bin/llama-server"; ok "llama-server $TAG (prebuilt)"
    else
      echo "  - prebuilt binary unusable on this OS; building from source (CPU-only, ~10 min)"
      command -v cmake >/dev/null || { echo "  ✗ cmake missing (brew install cmake / apt install cmake)"; exit 1; }
      [ -d "$HOMED/pkg/src" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$HOMED/pkg/src"
      cmake -S "$HOMED/pkg/src" -B "$HOMED/pkg/src/build" -DGGML_METAL=OFF -DGGML_BLAS=OFF -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release >/dev/null
      cmake --build "$HOMED/pkg/src/build" --target llama-server -j 4 >/dev/null
      ok "llama-server built"
    fi
  fi
  if [ "$MODEL" = 1 ]; then
    say "weak-lane model: Qwen3-1.7B Q8_0 (ModelScope, resumable)"
    F="$MODELS/Qwen3-1.7B-Q8_0.gguf"; URL="https://modelscope.cn/models/Qwen/Qwen3-1.7B-GGUF/resolve/master/Qwen3-1.7B-Q8_0.gguf"
    for _ in $(seq 1 40); do
      sz=$(stat -f%z "$F" 2>/dev/null || stat -c%s "$F" 2>/dev/null || echo 0)
      [ "$sz" -ge 1834000000 ] && break
      curl -sL -C - --max-time 550 -o "$F" "$URL" || true
    done
    head -c 4 "$F" 2>/dev/null | grep -q GGUF && ok "model ready ($F)" || echo "  ✗ model download incomplete; rerun install"
  fi
fi

case "$START" in
  init) say "starting: cluster init"; exec "$REPO/bin/cluster" init ;;
  join) say "starting: cluster join $JOIN_URL"; exec "$REPO/bin/cluster" join "$JOIN_URL" --token "$JOIN_TOKEN" ;;
  *) say "installed. Start with: bin/cluster init   (first device)   or   bin/cluster join <hub> --token <t>" ;;
esac
