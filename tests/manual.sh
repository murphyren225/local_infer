#!/usr/bin/env bash
# Manual walk-through of the 7 links, as literal curl commands (run from anywhere):
#   bash tests/manual.sh        # all links
#   bash tests/manual.sh 6      # one link
# Each command is echoed before it runs, so the output can be read next to the command.
# Session ids / nonces are fresh per run: Switchyard keys sessions by content and would
# otherwise reuse an already-latched session.
set -u
cd "$(dirname "$0")/.." || exit 1
REPO=$(pwd)
PAIR_HOME="$HOME/Library/Application Support/Nvidia Corporation/Personal AI Router"
N=$(date +%s | tail -c 6)
J='-H Content-Type:application/json'
NT='"reasoning_effort":"none","chat_template_kwargs":{"enable_thinking":false}'

run() { echo; echo "\$ $*"; eval "$*"; echo; }
title() { echo; echo "================ $*"; }
want=${1:-all}
pick() { [ "$want" = all ] || [ "$want" = "$1" ]; }

pick 1 && {
title "link 1: engines direct"
run "curl -s http://127.0.0.1:1234/v1/models"
run "curl -s http://127.0.0.1:1234/v1/chat/completions $J -d '{\"model\":\"qwen3-1.7b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: sglang-ok\"}],\"max_tokens\":20,$NT}'"
run "curl -s http://127.0.0.1:11434/api/tags | python3 -c \"import sys,json;print([m['name'] for m in json.load(sys.stdin)['models']])\""
run "curl -s http://127.0.0.1:11435/v1/chat/completions $J -d '{\"model\":\"qwen3:1.7b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: mac-ok\"}],\"max_tokens\":20,$NT}'"
}

pick 2 && {
title "link 2: PAIR on the Mac sees the 4090"
run "curl -s http://127.0.0.1:11234/v1/models"
run "curl -s http://127.0.0.1:21434/v1/models | python3 -c \"import sys,json;print([m['id'] for m in json.load(sys.stdin)['data']])\""
run "cat \"$PAIR_HOME/configs/manual-nodes.json\""
}

ledger() {
python3 - "$PAIR_HOME" "$1" << 'EOF'
import json, sys, os
home, n = sys.argv[1], int(sys.argv[2])
me = json.load(open(os.path.join(home, "node-id.json")))["node_uuid"]
ws = json.load(open(os.path.join(home, "workloads-history.json")))
ws = ws.get("workloads", ws) if isinstance(ws, dict) else ws
for w in ws[-n:]:
    print(" ", w["model"], w["engine"], w["state"], "on", "this-mac" if w["scheduledOn"] == me else "autodl-4090")
EOF
}

pick 3 && {
title "link 3: PAIR picks hardware"
run "curl -s http://127.0.0.1:21434/v1/chat/completions $J -d '{\"model\":\"qwen3:8b\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: via-4090\"}],\"max_tokens\":20,$NT}'"
run "for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w \"%{http_code} \" http://127.0.0.1:21434/v1/chat/completions $J -d '{\"model\":\"qwen3:1.7b\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}],\"max_tokens\":6,$NT}' & done; wait; echo"
echo "\$ (PAIR ledger, last 8 — flushed asynchronously, waiting 5s)"; sleep 5; ledger 8
}

pick 4 && {
title "link 4: decider"
run "curl -s http://127.0.0.1:4100/health"
run "curl -s http://127.0.0.1:4100/decide $J -d '{\"session\":\"t\",\"turn\":1,\"task_type\":\"code\",\"summary\":\"prove this race condition fix\",\"first_user_text\":\"prove this race condition fix\"}'"
run "curl -s http://127.0.0.1:4100/decide $J -d '{\"session\":\"t\",\"turn\":1,\"summary\":\"translate hello to French\",\"first_user_text\":\"translate hello to French\"}'"
run "tail -1 $REPO/logs/decisions.jsonl | cut -c1-160"
}

pick 5 && {
title "link 5: Switchyard model routes"
run "curl -s http://127.0.0.1:4000/v1/chat/completions $J -d '{\"model\":\"small\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: small-ok\"}],\"max_tokens\":20,$NT}' | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d['model'], '→', d['choices'][0]['message']['content'])\""
run "curl -s http://127.0.0.1:4000/v1/chat/completions $J -d '{\"model\":\"large\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: large-ok\"}],\"max_tokens\":20,$NT}' | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d['model'], '→', d['choices'][0]['message']['content'])\""
echo "\$ (PAIR ledger, last 2, waiting 5s)"; sleep 5; ledger 2
}

pick 6 && {
title "link 6: Switchyard auto (session manual-$N)"
STATS="curl -s http://127.0.0.1:4000/v1/routing/stats | python3 -c \"import sys,json;print('decider calls:', json.load(sys.stdin)['classifier']['models'].get('decision-http',{}).get('calls'))\""
run "$STATS"
U1="[$N] Debug this race condition in the connection pool and prove the fix is correct."
M="import sys,json;print('served by', json.load(sys.stdin)['model'])"
H="-H x-task-type:code -H x-switchyard-session-id:manual-$N"
run "curl -s http://127.0.0.1:4000/v1/chat/completions $J $H -d '{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"$U1\"}],\"max_tokens\":40,$NT}' | python3 -c \"$M\""
run "curl -s http://127.0.0.1:4000/v1/chat/completions $J $H -d '{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"$U1\"},{\"role\":\"assistant\",\"content\":\"ok\"},{\"role\":\"user\",\"content\":\"It still fails under load. Root cause, please.\"}],\"max_tokens\":40,$NT}' | python3 -c \"$M\""
run "curl -s http://127.0.0.1:4000/v1/chat/completions $J $H -d '{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"$U1\"},{\"role\":\"assistant\",\"content\":\"ok\"},{\"role\":\"user\",\"content\":\"It still fails under load. Root cause, please.\"},{\"role\":\"assistant\",\"content\":\"ok\"},{\"role\":\"user\",\"content\":\"Now write the patch.\"}],\"max_tokens\":40,$NT}' | python3 -c \"$M\""
run "$STATS"
run "ls -t $REPO/logs/rl | head -3 | while read f; do python3 -c \"import json,sys;d=json.load(open('$REPO/logs/rl/'+sys.argv[1]));print(d.get('served_tier'), (d.get('decision') or {}).get('route'))\" \"\$f\"; done"
}

pick 7 && {
title "link 7: personal-side local service (session manual7-$N)"
run "diff <(curl -s http://127.0.0.1:7000/v1/models) <(curl -s http://127.0.0.1:4000/v1/models) && echo 'local == hub'"
run "curl -s http://127.0.0.1:7000/v1/chat/completions $J -H x-task-type:extract -H x-switchyard-session-id:manual7-$N -d '{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"[$N] Reply with exactly: client-ok\"}],\"max_tokens\":12,$NT}' | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d['model'], '→', d['choices'][0]['message']['content'])\""
run "tail -1 $REPO/logs/decisions.jsonl | python3 -c \"import sys,json;print('decider saw task_type =', json.load(sys.stdin)['state']['task_type'])\""
}

pick all && {
title "whole chain"
run "E2E_HUB=http://127.0.0.1:4000 E2E_PAIR_RPC= python3 $REPO/tests/e2e_chain.py"
}
