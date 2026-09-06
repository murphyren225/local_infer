#!/usr/bin/env bash
# homed 编排入口 — 唯一需要用户运行的脚本。
#
#   pi (harness) → Switchyard :4000 (router) → vLLM :8001/:8002 → 云端兜底
#
# 各组件(homed/*/)通过文件和 HTTP 通信,互不 import;本脚本只负责按顺序拉起:
#   inference 车道(按 preset) → router 配置 → switchyard → harness 接线
#   → console (:6006) → failover 看门狗
#
#   ./homed/run_cluster.sh          # 启动(幂等:健康的组件跳过)
#   ./homed/run_cluster.sh stop     # 全停
#
# 换模型: INFERENCE_PRESET=<名字>(见 homed/inference/presets/),默认 qwen3-24gb
# 云端兜底: 配 TOGETHER_API_KEY 后,降级/兜底自动走真云端
set -euo pipefail
cd "$(dirname "$0")/.."

LARGE_PORT=8001
SMALL_PORT=8002
ROUTER_PORT=${ROUTER_PORT:-4000}

if [ "${1:-start}" = "stop" ]; then
  for name in watchdog console switchyard vllm-small vllm-large; do
    if [ -f "logs/$name.pid" ]; then
      kill "$(cat logs/$name.pid)" 2>/dev/null && echo "stopped $name" || true
      rm -f "logs/$name.pid"
    fi
  done
  exit 0
fi

# ---- 组件: inference —— 按 preset 启动本地车道 ----
INFERENCE_PRESET=${INFERENCE_PRESET:-qwen3-24gb}
# shellcheck disable=SC1090
. "homed/inference/presets/$INFERENCE_PRESET.env"
export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}

mkdir -p logs
# 车道名落盘,供 router 组件(gen_routes)独立读取 —— 组件间不共享 shell 变量
printf "LARGE_NAME=%s\nSMALL_NAME=%s\n" "$LARGE_NAME" "$SMALL_NAME" > logs/lanes.env

wait_http() {
  local url=$1 name=$2 tries=${3:-240}
  for _ in $(seq 1 "$tries"); do
    curl -sf "$url" >/dev/null 2>&1 && { echo "    $name healthy"; return 0; }
    sleep 5
  done
  echo "$name failed to become healthy — check logs/"; exit 1
}

start_lane() { # start_lane <name> <model_path> <served_name> <port> <args>
  local name=$1 path=$2 served=$3 port=$4 args=$5
  echo "==> vllm-$name: $path"
  if curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "    already healthy, skipping"; return 0
  fi
  # shellcheck disable=SC2086
  nohup vllm serve "$path" --served-model-name "$served" --port "$port" $args \
    > "logs/vllm-$name.log" 2>&1 &
  echo $! > "logs/vllm-$name.pid"
  wait_http "http://127.0.0.1:$port/health" "vllm-$name"
}

# 顺序有讲究:大模型必须先起(装载瞬时峰值需要整卡,见 failover/heal.sh)
start_lane large "$LARGE_MODEL_PATH" "$LARGE_NAME" $LARGE_PORT "$LARGE_ARGS"
start_lane small "$SMALL_MODEL_PATH" "$SMALL_NAME" $SMALL_PORT "$SMALL_ARGS"

# ---- 组件: router —— 按实时健康生成路由,重启 switchyard ----
if [ -f logs/switchyard.pid ]; then
  kill "$(cat logs/switchyard.pid)" 2>/dev/null || true
  rm -f logs/switchyard.pid
  sleep 1
fi
./homed/router/gen_routes.sh
echo "==> switchyard on :$ROUTER_PORT"
nohup switchyard serve --routing-profiles homed/router/routes.generated.yaml \
  --host 0.0.0.0 --port "$ROUTER_PORT" --inbound both \
  > logs/switchyard.log 2>&1 &
echo $! > logs/switchyard.pid
wait_http "http://127.0.0.1:$ROUTER_PORT/v1/models" switchyard 24

# ---- 组件: harness —— pi 的 provider 接线 ----
mkdir -p ~/.pi/agent
sed "s/ROUTER_PORT/$ROUTER_PORT/" homed/harness/pi-models.json > ~/.pi/agent/models.json
echo "==> pi provider written to ~/.pi/agent/models.json"

# ---- 组件: console —— Web Surface (:6006,AutoDL 自定义服务可直接暴露) ----
if curl -sf http://127.0.0.1:6006/api/status >/dev/null 2>&1; then
  echo "==> console already running"
else
  nohup python homed/console/console.py > logs/console.log 2>&1 &
  echo $! > logs/console.pid
  echo "==> console on :6006"
fi

# ---- 组件: failover —— 看门狗(云端兜底 + 自愈) ----
if [ -f logs/watchdog.pid ] && kill -0 "$(cat logs/watchdog.pid)" 2>/dev/null; then
  echo "==> watchdog already running"
else
  setsid nohup ./homed/failover/watchdog.sh > /dev/null 2>&1 < /dev/null &
  echo $! > logs/watchdog.pid
  echo "==> watchdog started"
fi

echo
echo "==> cluster is up. The whole API surface:"
echo "    POST http://127.0.0.1:$ROUTER_PORT/v1/chat/completions"
echo "    model: auto | small | large | cloud"
echo "    console: http://127.0.0.1:6006    try: ./homed/test.sh all"
