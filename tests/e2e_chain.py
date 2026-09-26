"""End-to-end check of the routing chain, run from the personal side (the Mac):

    local service (:7000) → hub Switchyard fork → decider → PAIR OpenAI proxy → SGLang

Not collected by pytest (no test_ prefix); run it by hand once the stack is up:

    python3 tests/e2e_chain.py

Endpoints come from the environment so the same script works with a tunnel or on a LAN:
    E2E_LOCAL     personal-side local service      (default http://127.0.0.1:7000)
    E2E_HUB       Switchyard as seen from here     (default http://127.0.0.1:14000, the tunnel)
    E2E_PAIR_RPC  PAIR broker JSON-RPC host:port   (default 127.0.0.1:17998, the tunnel; "" to skip)

What it proves, and where the evidence lives:
  1. A direct lane (`small`) answers → model route + PAIR proxy + engine all work.
  2. A 3-turn `auto` conversation with `x-task-type: code` → the decider is consulted on the
     early turns, the session latches to the strong pool after `confirmations` agreeing verdicts,
     and later turns skip the judge. Switchyard's routing stats count the judge calls; the decider
     appends every decision to logs/decisions.jsonl; the RL traces (--rl-log-dir) carry
     `decision` and `served_tier` per request; PAIR's ledger shows which engine served each job.
The first user message carries a nonce because Switchyard derives the session key from content:
replaying an identical opening would reuse an already-latched session and skip the judge.
"""
import json
import os
import socket
import sys
import time
import urllib.request

LOCAL = os.environ.get("E2E_LOCAL", "http://127.0.0.1:7000").rstrip("/")
HUB = os.environ.get("E2E_HUB", "http://127.0.0.1:14000").rstrip("/")
_rpc = os.environ.get("E2E_PAIR_RPC", "127.0.0.1:17998")
PAIR_RPC = (_rpc.rsplit(":", 1)[0], int(_rpc.rsplit(":", 1)[1])) if _rpc else None
SID = "chain-" + str(int(time.time()))[-6:]


def chat(model, messages, task_type=None, session=None, max_tokens=80):
    h = {"Content-Type": "application/json"}
    if task_type:
        h["x-task-type"] = task_type
    if session:
        h["x-switchyard-session-id"] = session
    # Thinking off on every engine we route to: SGLang/vLLM read chat_template_kwargs,
    # Ollama's OpenAI endpoint reads reasoning_effort. Unknown keys are ignored by the other.
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}
    req = urllib.request.Request(LOCAL + "/v1/chat/completions", method="POST", headers=h,
                                 data=json.dumps(body).encode())
    t = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    return d, time.time() - t


def answer(d) -> str:
    m = d["choices"][0]["message"]
    return (m.get("content") or m.get("reasoning") or m.get("reasoning_content") or "").strip()


def rpc(method, params=None, timeout=20):
    if not PAIR_RPC:
        return None
    s = socket.create_connection(PAIR_RPC, timeout=timeout + 5)
    s.sendall((json.dumps({"method": method, "params": params, "timeout": timeout}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        c = s.recv(65536)
        if not c:
            break
        buf += c
    return json.loads(buf).get("result")


def main() -> int:
    ok = True
    print("=== 1. direct lane `small` → Switchyard model route → PAIR proxy → SGLang")
    d, dt = chat("small", [{"role": "user", "content": "Reply with exactly: chain-ok"}])
    text = answer(d)
    print(f"  model={d['model']} {dt:.1f}s tokens={d.get('usage', {}).get('completion_tokens')} :: {text[:60]!r}")
    ok &= "chain-ok" in text

    print("=== 2. `auto` 3-turn conversation, x-task-type=code, sticky session")
    hist = []
    for text in (f"[{SID}] Debug this race condition in the connection pool and prove the fix is correct.",
                 "It still fails under load. Root cause, please.",
                 "Now write the patch."):
        hist.append({"role": "user", "content": text})
        d, dt = chat("auto", hist, task_type="code", session=SID)
        ans = answer(d)
        hist.append({"role": "assistant", "content": ans})
        print(f"  turn {len(hist) // 2}: model={d['model']} {dt:.1f}s :: {ans[:50]!r}")

    print("=== 3. evidence")
    stats = json.load(urllib.request.urlopen(HUB + "/v1/routing/stats", timeout=10))
    calls = {k: v.get("calls") for k, v in stats.get("classifier", {}).get("models", {}).items()}
    print("  switchyard judge calls (cumulative):", calls)
    ok &= bool(calls)
    if PAIR_RPC:
        ws = (rpc("workloads:get-initial") or {}).get("workloads", [])[-5:]
        print("  PAIR ledger (last 5):", [(w["model"], w["engine"], w["state"]) for w in ws])
        eng = (rpc("engine:get-installed") or {}).get("engines", [])
        print("  PAIR engines:", [(e["engine"], e["display_name"], e["running"], e["port"]) for e in eng])
    print("  next: decider log  → logs/decisions.jsonl on the hub (task_type must be 'code' for this session)")
    print("        RL traces    → Switchyard --rl-log-dir: served_tier weak → strong → strong, decision on the first two")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
