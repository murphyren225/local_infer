#!/usr/bin/env bash
# 看门狗: 本地大模型靠不住时,自动切换到云端 API / 幸存车道,并在后台自愈。
#
#   车道连续 2 次健康检查失败 → gen_routes.sh 重生成降级路由 → 重启 Switchyard
#   (秒级完成,Pi/curl 用户只感觉到"这几条回答换了个模型");
#   同时后台调 run_cluster.sh 拉起崩掉的模型,恢复后自动切回正常路由。
#
# 由 run_cluster.sh 启动;单独跑: ./homed/watchdog.sh
set -uo pipefail
cd "$(dirname "$0")/../.."

INTERVAL=${WATCHDOG_INTERVAL:-10}
ROUTER_PORT=${ROUTER_PORT:-4000}
log() { echo "$(date '+%F %T') watchdog: $*" >> logs/watchdog.log; }

restart_switchyard() {
  [ -f logs/switchyard.pid ] && kill "$(cat logs/switchyard.pid)" 2>/dev/null
  sleep 1
  nohup switchyard serve --routing-profiles homed/router/routes.generated.yaml \
    --host 0.0.0.0 --port "$ROUTER_PORT" --inbound both \
    > logs/switchyard.log 2>&1 &
  echo $! > logs/switchyard.pid
}

fails_large=0; fails_small=0
current=$(cat logs/cluster_mode 2>/dev/null || echo normal)
log "started (interval=${INTERVAL}s, mode=$current)"

while true; do
  sleep "$INTERVAL"
  # 修复进行中: 车道起落由 heal.sh 全权指挥,看门狗不插手,避免路由抖动
  [ -f logs/heal.lock ] && continue
  curl -sf --max-time 3 http://127.0.0.1:8001/health >/dev/null 2>&1 \
    && fails_large=0 || fails_large=$((fails_large + 1))
  curl -sf --max-time 3 http://127.0.0.1:8002/health >/dev/null 2>&1 \
    && fails_small=0 || fails_small=$((fails_small + 1))

  large_ok=1; small_ok=1
  [ "$fails_large" -ge 2 ] && large_ok=0
  [ "$fails_small" -ge 2 ] && small_ok=0

  if [ $large_ok = 1 ] && [ $small_ok = 1 ]; then desired=normal; else desired=degraded; fi
  now=$(cat logs/cluster_mode 2>/dev/null || echo normal)
  case "$now" in normal) now_kind=normal ;; *) now_kind=degraded ;; esac

  if [ "$desired" != "$now_kind" ]; then
    log "health change (large_ok=$large_ok small_ok=$small_ok) → regenerating routes"
    if ./homed/router/gen_routes.sh >> logs/watchdog.log 2>&1; then
      restart_switchyard
      log "switchyard restarted in mode: $(cat logs/cluster_mode)"
    else
      log "gen_routes failed — all lanes dead and no cloud key; leaving router as-is"
    fi
  fi

  # 自愈: 交给 heal.sh(大模型挂掉时需要分段重启,见其头部注释)。
  # Hub 模式(WATCHDOG_HEAL=0)只做降级/恢复切换,不尝试本地拉起——
  # 远端 GPU 的生死由隧道重连和对端自己负责。
  if [ "${WATCHDOG_HEAL:-1}" = 1 ] && [ "$desired" = degraded ] && [ ! -f logs/heal.lock ]; then
    touch logs/heal.lock
    log "self-heal: dispatching heal.sh in background"
    setsid nohup bash -c "./homed/failover/heal.sh; rm -f logs/heal.lock" \
      > /dev/null 2>&1 < /dev/null &
  fi
done
