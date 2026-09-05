#!/usr/bin/env bash
# Component tests for the home cluster. Each layer testable on its own:
#
#   ./homed/test.sh small     # inference lane: small model direct (:8002)
#   ./homed/test.sh large     # inference lane: large model direct (:8001)
#   ./homed/test.sh router    # Switchyard: lanes + escalation via :4000
#   ./homed/test.sh pi        # harness: pi one-shot through the router
#   ./homed/test.sh all       # everything, in order
set -euo pipefail
cd "$(dirname "$0")/.."

GW=${GW:-http://127.0.0.1:4000/v1}
PY=$(command -v python3 || echo /root/miniconda3/bin/python)

ask() { # ask <base_url> <model> <prompt> [max_tokens]
  curl -s --max-time 180 "$1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"$3\"}],\"max_tokens\":${4:-256}}" \
  | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
if "error" in d: print("  ERROR:", d["error"]); sys.exit(1)
print("  answered-by:", d.get("model"))
c = d["choices"][0]["message"].get("content") or ""
print("  answer:", " ".join(c.split())[:220])'
}

t_small() {
  echo "== 小模型直连 (vLLM :8002, 绕过路由) =="
  ask http://127.0.0.1:8002/v1 qwen3-1.7b-fp8 "用一句话介绍你自己"
}

t_large() {
  echo "== 大模型直连 (vLLM :8001, 绕过路由) =="
  ask http://127.0.0.1:8001/v1 qwen3-32b-awq "用一句话介绍你自己"
}

t_router() {
  echo "== Router: 模型目录 =="
  curl -s "$GW/models" | "$PY" -c \
    'import json,sys; print(" ", [m["id"] for m in json.load(sys.stdin)["data"]])'
  echo "== Router: 显式 small 车道 =="
  ask "$GW" small "把这句话翻译成英文:今天天气不错" 128
  echo "== Router: 显式 large 车道 =="
  ask "$GW" large "为什么分布式系统需要共识算法?一句话回答" 256
  echo "== Router: auto (先小后判,该升级就升级) =="
  ask "$GW" auto "证明 sqrt(2) 是无理数,给出完整推理" 512
  echo "== Router: cloud 车道 =="
  ask "$GW" cloud "说 hi" 32
  echo "== Router: 路由统计 (/v1/routing/stats) =="
  curl -s "$GW/routing/stats" | "$PY" -c \
    'import json,sys; d=json.load(sys.stdin); print("  requests:", d.get("total_requests"), " tokens:", d.get("total_tokens",{}).get("total"))'
}

t_pi() {
  echo "== Pi 单发指令 (pi -p, 走 home/auto) =="
  pi --provider home --model auto -p "用一句话说明你运行在什么模型栈上"
  echo "== Pi 工具调用 (真实写文件) =="
  rm -f /tmp/pi_e2e.txt
  (cd /tmp && pi --provider home --model auto -p "创建文件 /tmp/pi_e2e.txt,内容是 e2e-ok" >/dev/null 2>&1) || true
  if [ "$(cat /tmp/pi_e2e.txt 2>/dev/null)" = "e2e-ok" ]; then
    echo "  PASS: pi 通过本地模型调工具写入了 /tmp/pi_e2e.txt"
  else
    echo "  FAIL: 文件未创建"; exit 1
  fi
}

t_console() {
  echo "== 控制台: 集群状态 =="
  curl -s http://127.0.0.1:6006/api/status | "$PY" -c \
    'import json,sys; d=json.load(sys.stdin); print("  mode:", d["mode"], " lanes:", d["lanes"], " 升级事件数:", len(d["escalations"]))'
  echo "== 控制台: 文件上传 + 让模型总结 =="
  printf "第一季度营收 120 万,成本 80 万,利润 40 万。\n第二季度营收 150 万,成本 90 万,利润 60 万。\n" > /tmp/report.txt
  UPLOADED=$(curl -s -F "file=@/tmp/report.txt" http://127.0.0.1:6006/api/upload)
  echo "$UPLOADED" | "$PY" -c \
    'import json,sys; d=json.load(sys.stdin); print("  uploaded:", d["name"], d["chars"], "chars")'
  TEXT=$(echo "$UPLOADED" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["text"])')
  curl -s http://127.0.0.1:6006/api/chat -H 'Content-Type: application/json' \
    -d "$("$PY" -c "import json,sys; print(json.dumps({'model':'small','messages':[{'role':'user','content':'总结这份报表的要点:'+open('/tmp/report.txt').read()}],'max_tokens':256}))")" \
  | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print("  answered-by:", d.get("model"), " 延迟:", d.get("_console",{}).get("latency_ms"), "ms")
print("  answer:", " ".join((d["choices"][0]["message"].get("content") or "").split())[:150])'
}

t_failover() {
  echo "== 兜底演练: 故意杀掉 32B,看路由是否自动切换 =="
  kill "$(cat logs/vllm-large.pid 2>/dev/null)" 2>/dev/null || pkill -f "qwen3-32[b]" || true
  echo "  已杀掉大模型,等看门狗切换(最多 90 秒)..."
  for i in $(seq 1 18); do
    MODE=$(cat logs/cluster_mode 2>/dev/null)
    [ "$MODE" != normal ] && [ -n "$MODE" ] && break
    sleep 5
  done
  echo "  当前模式: $(cat logs/cluster_mode)"
  echo "  降级期间派活给 auto(应由幸存车道/云端接住):"
  sleep 5   # 路由器重启需要 1-2 秒,稍等再问
  answered=0
  for attempt in 1 2 3; do
    if ask "$GW" auto "说一句话证明你还活着" 64; then answered=1; break; fi
    echo "  (第 $attempt 次没打通,重试...)"; sleep 5
  done
  if [ "$answered" = 0 ]; then
    if [ -z "${TOGETHER_API_KEY:-}" ]; then
      echo "  (符合预期: 未配云端 key 时,分段自愈会撤下小模型腾显存,期间短暂停机;"
      echo "   配上 TOGETHER_API_KEY 后此窗口流量全走云端,零中断)"
    else
      echo "  FAIL: 配了云端 key 仍无法应答"; exit 1
    fi
  fi
  echo "  等待看门狗自愈(重新加载 32B,最多 8 分钟)..."
  for i in $(seq 1 96); do
    [ "$(cat logs/cluster_mode 2>/dev/null)" = normal ] && break
    sleep 5
  done
  if [ "$(cat logs/cluster_mode)" = normal ]; then
    echo "  PASS: 已自动恢复正常模式,大模型复活"
    sleep 5
    for attempt in 1 2 3; do
      if ask "$GW" large "说 ok" 32; then break; fi
      sleep 5
    done
  else
    echo "  FAIL: 8 分钟内未恢复,看 logs/watchdog.log 和 logs/heal.log"; exit 1
  fi
}

case "${1:-all}" in
  small)   t_small ;;
  large)   t_large ;;
  router)  t_router ;;
  pi)      t_pi ;;
  console) t_console ;;
  failover) t_failover ;;
  all)    t_small; echo; t_large; echo; t_router; echo; t_console; echo; t_pi ;;
  *) echo "usage: $0 [small|large|router|pi|console|failover|all]  (failover 是破坏性演练,不含在 all 里)"; exit 1 ;;
esac
