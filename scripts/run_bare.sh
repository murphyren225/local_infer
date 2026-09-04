#!/usr/bin/env bash
# Run Tandem WITHOUT Docker: two vLLM instances + gateway as bare processes.
# For environments where Docker isn't available (AutoDL & other rented GPU
# containers, or a box where you just pip-installed vllm).
#
# Reads .env (written by autotune/autotune.py) or falls back to 24GB-card
# defaults. Logs go to ./logs/, PIDs to ./logs/*.pid.
#
#   ./scripts/run_bare.sh          # start everything
#   ./scripts/run_bare.sh stop     # stop everything
set -euo pipefail
cd "$(dirname "$0")/.."

LARGE_PORT=${LARGE_PORT:-8001}
SMALL_PORT=${SMALL_PORT:-8002}
GATEWAY_PORT=${GATEWAY_PORT:-8080}

if [ "${1:-start}" = "stop" ]; then
  for name in gateway vllm-small vllm-large; do
    if [ -f "logs/$name.pid" ]; then
      kill "$(cat logs/$name.pid)" 2>/dev/null && echo "stopped $name" || true
      rm -f "logs/$name.pid"
    fi
  done
  exit 0
fi

# shellcheck disable=SC1091
[ -f .env ] && set -a && source .env && set +a
# Defaults = the 24GB profile measured on a real RTX 4090D (2026-09-04).
LARGE_MODEL=${LARGE_MODEL:-Qwen/Qwen3-32B-AWQ}
LARGE_QUANT=${LARGE_QUANT:-auto}
LARGE_GPU_UTIL=${LARGE_GPU_UTIL:-0.83}
LARGE_MAX_LEN=${LARGE_MAX_LEN:-6144}
LARGE_KV_DTYPE=${LARGE_KV_DTYPE:-fp8}
LARGE_MAX_SEQS=${LARGE_MAX_SEQS:-16}
LARGE_MAX_BATCHED=${LARGE_MAX_BATCHED:-2048}
LARGE_EAGER=${LARGE_EAGER:-1}
SMALL_MODEL=${SMALL_MODEL:-Qwen/Qwen3-1.7B-FP8}
SMALL_QUANT=${SMALL_QUANT:-auto}
SMALL_GPU_UTIL=${SMALL_GPU_UTIL:-0.11}
SMALL_MAX_LEN=${SMALL_MAX_LEN:-4096}
SMALL_KV_DTYPE=${SMALL_KV_DTYPE:-fp8}
SMALL_MAX_SEQS=${SMALL_MAX_SEQS:-8}
SMALL_MAX_BATCHED=${SMALL_MAX_BATCHED:-512}
SMALL_EAGER=${SMALL_EAGER:-1}
# Two co-resident instances run close to the VRAM limit; this avoids
# fragmentation OOMs (measured to matter on 24GB cards).
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}

# Build per-lane flags. "auto"/"null"/empty quantization → let vLLM
# auto-detect (it picks the fast awq_marlin kernel; forcing "awq"
# selects the unoptimized one).
lane_flags() {
  local quant=$1 util=$2 len=$3 kv=$4 seqs=$5 batched=$6 eager=$7
  local flags="--gpu-memory-utilization $util --max-model-len $len"
  flags="$flags --max-num-seqs $seqs --max-num-batched-tokens $batched"
  case "$quant" in auto|null|"") ;; *) flags="$flags --quantization $quant" ;; esac
  case "$kv" in auto|null|"") ;; *) flags="$flags --kv-cache-dtype $kv" ;; esac
  [ "$eager" = "1" ] && flags="$flags --enforce-eager"
  echo "$flags"
}
LARGE_FLAGS=$(lane_flags "$LARGE_QUANT" "$LARGE_GPU_UTIL" "$LARGE_MAX_LEN" \
  "$LARGE_KV_DTYPE" "$LARGE_MAX_SEQS" "$LARGE_MAX_BATCHED" "$LARGE_EAGER")
SMALL_FLAGS=$(lane_flags "$SMALL_QUANT" "$SMALL_GPU_UTIL" "$SMALL_MAX_LEN" \
  "$SMALL_KV_DTYPE" "$SMALL_MAX_SEQS" "$SMALL_MAX_BATCHED" "$SMALL_EAGER")

mkdir -p logs

wait_healthy() {
  local port=$1 name=$2
  for _ in $(seq 1 240); do
    curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1 && { echo "    $name (:$port) healthy"; return 0; }
    sleep 5
  done
  echo "$name on :$port failed to become healthy — check logs/"; exit 1
}

# Sequential on purpose: on a tight 24GB card, letting both instances
# profile GPU memory at the same time risks a transient OOM.
echo "==> starting vllm-large: $LARGE_MODEL (util $LARGE_GPU_UTIL, len $LARGE_MAX_LEN)"
# shellcheck disable=SC2086
nohup vllm serve "$LARGE_MODEL" $LARGE_FLAGS --port "$LARGE_PORT" \
  > logs/vllm-large.log 2>&1 &
echo $! > logs/vllm-large.pid
echo "==> waiting for vllm-large (model load can take minutes)"
wait_healthy "$LARGE_PORT" vllm-large

echo "==> starting vllm-small: $SMALL_MODEL (util $SMALL_GPU_UTIL, len $SMALL_MAX_LEN)"
# shellcheck disable=SC2086
nohup vllm serve "$SMALL_MODEL" $SMALL_FLAGS --port "$SMALL_PORT" \
  > logs/vllm-small.log 2>&1 &
echo $! > logs/vllm-small.pid
wait_healthy "$SMALL_PORT" vllm-small

if [ ! -f config/tandem.yaml ]; then
  echo "==> writing config/tandem.yaml (localhost lanes)"
  cat > config/tandem.yaml <<EOF
lanes:
  large:
    base_url: http://127.0.0.1:$LARGE_PORT/v1
    model: $LARGE_MODEL
  small:
    base_url: http://127.0.0.1:$SMALL_PORT/v1
    model: $SMALL_MODEL
EOF
fi

echo "==> starting gateway on :$GATEWAY_PORT"
TANDEM_CONFIG=config/tandem.yaml nohup python -m uvicorn tandem_gateway.main:app \
  --app-dir gateway --host 0.0.0.0 --port "$GATEWAY_PORT" \
  > logs/gateway.log 2>&1 &
echo $! > logs/gateway.pid

sleep 3
curl -sf "http://127.0.0.1:$GATEWAY_PORT/healthz" && echo && echo "==> tandem is up"
