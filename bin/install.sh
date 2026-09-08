#!/usr/bin/env bash
# Install prerequisites for a homed node (idempotent). Run once per device, then bin/homed init|join.
#
#   bin/install.sh            # everything this device needs
#   bin/install.sh --no-model # skip the weak-lane model download
#
# What it does, by layer (docs/design.md):
#   调度层  Python >= 3.12 venv at ~/.homed/venv with nemo-switchyard + console deps
#   接入层  Pi CLI via npm (if npm present), extensions synced later by `homed init`
#   资源层  NVIDIA box: prints the vLLM + model steps (environment-specific, not automated here)
#           CPU/Mac box: llama.cpp binary (download, or build when the OS is too old) + 1.7B GGUF
set -euo pipefail
HOMED="$HOME/.homed"
VENV="$HOMED/venv"
MODELS="$HOMED/models"
mkdir -p "$HOMED" "$MODELS" "$HOMED/bin" "$HOMED/pkg"
say() { printf '\n== %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }

# ---- 调度层: venv + switchyard + console deps ----------------------------
say "Python venv for services (needs Python >= 3.12)"
PY=""
for c in python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then PY=$c; break; fi
done
[ -n "$PY" ] || { echo "  ✗ need Python >= 3.12 (brew install python@3.12 / apt install python3.12)"; exit 1; }
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q nemo-switchyard fastapi uvicorn httpx python-multipart pyyaml
ok "switchyard $("$VENV/bin/pip" show nemo-switchyard | awk '/^Version/{print $2}') in $VENV"

# ---- 接入层: Pi -------------------------------------------------------------
say "Pi CLI"
if command -v pi >/dev/null; then
  ok "pi $(pi --version 2>/dev/null | head -1) already installed"
elif command -v npm >/dev/null; then
  npm install -g --ignore-scripts @earendil-works/pi-coding-agent >/dev/null 2>&1 && ok "pi installed" \
    || echo "  ✗ npm install failed; install Node >= 22 and retry"
else
  echo "  - npm not found; Pi is optional on pure inference nodes. On user machines: install Node >= 22 then"
  echo "    npm install -g --ignore-scripts @earendil-works/pi-coding-agent"
fi

# ---- 资源层 ---------------------------------------------------------------
if command -v nvidia-smi >/dev/null; then
  say "NVIDIA node: vLLM lane (not automated — environment specific)"
  echo "  1. pip install vllm            (in the Python env you will run the engine from)"
  echo "  2. download weights to a local dir, e.g. (ModelScope inside China):"
  echo "       modelscope download --model Qwen/Qwen3-32B-AWQ  --local_dir $MODELS/Qwen3-32B-AWQ"
  echo "       modelscope download --model Qwen/Qwen3-1.7B-FP8 --local_dir $MODELS/Qwen3-1.7B-FP8"
  echo "  3. set LARGE_MODEL_PATH / SMALL_MODEL_PATH if not using $MODELS, then: bin/homed init"
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
  if [ "${1:-}" != "--no-model" ]; then
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

say "done. Next: bin/homed init   (first device)   or   bin/homed join <hub> --token <t>"
