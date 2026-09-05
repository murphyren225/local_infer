#!/usr/bin/env bash
# 自愈执行器(由 watchdog 触发,也可手动跑)。
#
# 实测教训: 32B 冷启动的装载瞬时峰值 ~20.5GiB(AWQ marlin 重排临时多要 1GB),
# 高于稳态占用。首次启动整卡空闲没问题;但小模型驻留 3.3GiB 时原地重启 32B
# 必 OOM。所以大模型挂掉时必须分段重启:
#   1) 撤下小模型,腾空整卡
#   2) 路由切换: 有 TOGETHER_API_KEY → 全部流量走云端(零中断);
#      没有 → 本地全灭,明确进入短暂停机(~5 分钟),日志留痕
#   3) run_cluster 按已验证顺序重启(大→小→正常路由)
# 只有小模型挂掉时无此问题(小模型在大模型旁启动是首启就验证过的顺序)。
set -uo pipefail
cd "$(dirname "$0")/../.."
ROUTER_PORT=${ROUTER_PORT:-4000}
log() { echo "$(date '+%F %T') heal: $*" >> logs/watchdog.log; }

ok() { curl -sf --max-time 3 "http://127.0.0.1:$1/health" >/dev/null 2>&1; }

if ok 8001 && ok 8002; then
  log "both lanes healthy — nothing to heal"
  exit 0
fi

if ! ok 8001; then
  log "large dead → staged restart: evicting small to free the card"
  [ -f logs/vllm-small.pid ] && kill "$(cat logs/vllm-small.pid)" 2>/dev/null
  pkill -f "vllm serve.*1\.7[B]" 2>/dev/null || true
  sleep 3
  if ./homed/router/gen_routes.sh >> logs/watchdog.log 2>&1; then
    log "interim routes: $(cat logs/cluster_mode) (cloud key present → zero-downtime)"
  else
    log "no cloud key → full brownout while 32B reloads (~5 min)"
  fi
  [ -f logs/switchyard.pid ] && kill "$(cat logs/switchyard.pid)" 2>/dev/null
  sleep 1
  nohup switchyard serve --routing-profiles homed/router/routes.generated.yaml \
    --host 0.0.0.0 --port "$ROUTER_PORT" --inbound both \
    > logs/switchyard.log 2>&1 &
  echo $! > logs/switchyard.pid
fi

# 按已验证顺序拉起缺失车道;结束时按恢复后的健康状况重生成正常路由
./homed/run_cluster.sh >> logs/heal.log 2>&1
log "heal finished, mode=$(cat logs/cluster_mode 2>/dev/null)"
