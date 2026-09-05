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

case "${1:-all}" in
  small)  t_small ;;
  large)  t_large ;;
  router) t_router ;;
  pi)     t_pi ;;
  all)    t_small; echo; t_large; echo; t_router; echo; t_pi ;;
  *) echo "usage: $0 [small|large|router|pi|all]"; exit 1 ;;
esac
