#!/usr/bin/env bash
# 给任意车道派活的最简入口:
#   ./homed/ask.sh small "把这句话翻译成英文:今天很忙"
#   ./homed/ask.sh large "分析一下微服务的利弊"
#   ./homed/ask.sh auto  "随便什么任务,路由器自己决定谁干"
# 第一个参数是车道 (auto|small|large|cloud),后面全是你的任务。
set -euo pipefail
MODEL=${1:?用法: ask.sh <auto|small|large|cloud> "你的任务"}
shift
PROMPT=$*
PY=$(command -v python3 || echo /root/miniconda3/bin/python)

"$PY" - "$MODEL" "$PROMPT" <<'EOF'
import json, sys, urllib.request
model, prompt = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    "http://127.0.0.1:4000/v1/chat/completions",
    data=json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
    }).encode(),
    headers={"Content-Type": "application/json"},
)
d = json.load(urllib.request.urlopen(req, timeout=300))
print(f"[干活的模型: {d.get('model')}]")
print(d["choices"][0]["message"].get("content") or "(空)")
EOF
