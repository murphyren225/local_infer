"""Per-link tests for the routing chain, run from the Mac (the scheduler). One link at a time,
then the whole chain:

    python3 tests/links.py 1        # one link (1..7)
    python3 tests/links.py all      # links 1..7 in order, then tests/e2e_chain.py

Links (docs/pipeline-design.md §9):
  1 engines      the engines themselves answer: 4090 SGLang, 4090 Ollama, Mac Ollama
  2 pair-node    PAIR on the Mac sees the 4090: its models are in the Mac proxies' catalogues
  3 pair-route   PAIR picks hardware: 4090-only models land on the 4090 (Mac ledger)
  4 decider      the decision service returns a normalized route + task_type and logs it
  5 model-route  Switchyard model routes (small / large) reach PAIR and an engine
  6 auto-route   Switchyard `auto`: decider consulted, latch to strong, RL traces written
  7 client       the personal-side local service mirrors the hub and forwards x-task-type

Every endpoint is an environment variable with the current Mac topology as default:
  LINK_SGLANG      4090 SGLang via tunnel            http://127.0.0.1:1234
  LINK_BOX_OLLAMA  4090 Ollama engine via tunnel     http://127.0.0.1:11434
  LINK_MAC_OLLAMA  Mac Ollama engine (PAIR-managed)  http://127.0.0.1:11435
  LINK_NODEINFO    4090 node-info via tunnel         http://127.0.0.1:14318
  LINK_LM_PROXY    Mac PAIR LM-slot proxy            http://127.0.0.1:11234
  LINK_OL_PROXY    Mac PAIR Ollama proxy             http://127.0.0.1:21434
  LINK_DECIDER     decider                           http://127.0.0.1:4100
  LINK_HUB         Switchyard on the Mac             http://127.0.0.1:4000
  LINK_LOCAL       personal-side local service       http://127.0.0.1:7000
  LINK_PAIR_HOME   PAIR desktop app data dir (ledger, node id, manual nodes)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = os.environ.get
SGLANG = E("LINK_SGLANG", "http://127.0.0.1:1234")
BOX_OLLAMA = E("LINK_BOX_OLLAMA", "http://127.0.0.1:11434")
MAC_OLLAMA = E("LINK_MAC_OLLAMA", "http://127.0.0.1:11435")
NODEINFO = E("LINK_NODEINFO", "http://127.0.0.1:14318")
LM_PROXY = E("LINK_LM_PROXY", "http://127.0.0.1:11234")
OL_PROXY = E("LINK_OL_PROXY", "http://127.0.0.1:21434")
DECIDER = E("LINK_DECIDER", "http://127.0.0.1:4100")
HUB = E("LINK_HUB", "http://127.0.0.1:4000")
LOCAL = E("LINK_LOCAL", "http://127.0.0.1:7000")
PAIR_HOME = Path(E("LINK_PAIR_HOME", str(Path.home() / "Library/Application Support/Nvidia Corporation/Personal AI Router")))
DECIDER_LOG = ROOT / "logs" / "decisions.jsonl"
RL_DIR = ROOT / "logs" / "rl"
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}


# ---------------------------------------------------------------- helpers

class Check:
    def __init__(self, title: str):
        self.title, self.fails = title, 0
        print(f"=== {title}")

    def ok(self, cond, msg: str) -> bool:
        print(f"  {'✓' if cond else '✗'} {msg}")
        self.fails += 0 if cond else 1
        return bool(cond)

    def result(self) -> bool:
        print(f"RESULT {self.title}: {'PASS' if not self.fails else f'FAIL ({self.fails})'}\n")
        return not self.fails


def get(url: str, timeout: float = 10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else body.decode(errors="replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 300):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def models(base: str) -> list[str]:
    code, d = get(base + "/v1/models")
    return [m["id"] for m in d.get("data", [])] if code == 200 and isinstance(d, dict) else []


def chat(base: str, model: str, text: str, headers=None, max_tokens=24, extra=None):
    body = {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens, **NO_THINK,
            **(extra or {})}
    t = time.time()
    code, d = post(base + "/v1/chat/completions", body, headers)
    ans = ""
    if code == 200 and isinstance(d, dict) and d.get("choices"):
        m = d["choices"][0]["message"]
        ans = (m.get("content") or m.get("reasoning") or m.get("reasoning_content") or "").strip()
    return code, d, ans, time.time() - t


def ledger(since: float = 0) -> list[dict]:
    p = PAIR_HOME / "workloads-history.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text())
    ws = d.get("workloads", d) if isinstance(d, dict) else d
    return [w for w in ws if (w.get("createdAt") or 0) / 1000 >= since]


def ledger_wait(since: float, n: int, timeout: float = 15) -> list[dict]:
    """PAIR flushes workloads-history.json asynchronously; wait until n entries since `since` show up."""
    end = time.time() + timeout
    ws = ledger(since)
    while len(ws) < n and time.time() < end:
        time.sleep(1)
        ws = ledger(since)
    return ws


def node_names() -> dict[str, str]:
    names = {}
    try:
        names[json.loads((PAIR_HOME / "node-id.json").read_text())["node_uuid"]] = "this-mac"
    except (OSError, KeyError, ValueError):
        pass
    return names


def where(w: dict, names: dict[str, str]) -> str:
    return names.get(w.get("scheduledOn") or "", "remote:" + (w.get("scheduledOn") or "?")[:8])


def decider_tail(n: int = 1) -> list[dict]:
    if not DECIDER_LOG.exists():
        return []
    lines = DECIDER_LOG.read_text().splitlines()[-n:]
    return [json.loads(l) for l in lines]


def rl_after(t0: float) -> list[dict]:
    out = []
    for p in sorted(RL_DIR.glob("*.json")) if RL_DIR.exists() else []:
        if p.stat().st_mtime >= t0:
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                pass
    return out


def nonce() -> str:
    return str(int(time.time() * 1000))[-7:]


# ---------------------------------------------------------------- links

def link1_engines() -> bool:
    c = Check("link 1: engines answer directly")
    ms = models(SGLANG)
    c.ok(ms, f"4090 SGLang {SGLANG}: models {ms}")
    if ms:
        code, _, ans, dt = chat(SGLANG, ms[0], "Reply with exactly: sglang-ok")
        c.ok("sglang-ok" in ans, f"4090 SGLang chat → {ans[:30]!r} ({dt:.1f}s)")
    code, d = get(BOX_OLLAMA + "/api/tags")
    tags = [m["name"] for m in d.get("models", [])] if code == 200 and isinstance(d, dict) else []
    c.ok(tags, f"4090 Ollama {BOX_OLLAMA}: models {tags}")
    code, d = get(MAC_OLLAMA + "/api/tags")
    mtags = [m["name"] for m in d.get("models", [])] if code == 200 and isinstance(d, dict) else []
    c.ok(mtags, f"Mac Ollama {MAC_OLLAMA}: models {mtags}")
    if mtags:
        code, _, ans, dt = chat(MAC_OLLAMA, mtags[0], "Reply with exactly: mac-ok")
        c.ok("mac-ok" in ans, f"Mac Ollama chat → {ans[:30]!r} ({dt:.1f}s)")
    return c.result()


def link2_pair_node() -> bool:
    c = Check("link 2: PAIR on the Mac sees the 4090 (manual node)")
    code, _ = get(NODEINFO + "/")
    c.ok(code in (200, 404), f"4090 node-info reachable at {NODEINFO} (http {code})")
    box_sg = models(SGLANG)
    lm = models(LM_PROXY)
    c.ok(lm and set(box_sg) <= set(lm), f"LM-slot proxy {LM_PROXY} lists the 4090's SGLang models: {lm}")
    code, d = get(BOX_OLLAMA + "/api/tags")
    box_ol = [m["name"] for m in d.get("models", [])] if code == 200 and isinstance(d, dict) else []
    ol = models(OL_PROXY)
    c.ok(ol and set(box_ol) <= set(ol), f"Ollama proxy {OL_PROXY} lists the 4090's Ollama models: {ol}")
    mn = PAIR_HOME / "configs" / "manual-nodes.json"
    c.ok(mn.exists(), f"manual node persisted: {mn.read_text().strip() if mn.exists() else 'missing'}")
    return c.result()


def link3_pair_route() -> bool:
    c = Check("link 3: PAIR picks hardware")
    names = node_names()
    t0 = time.time()
    lm = models(LM_PROXY)
    if lm:
        code, _, ans, dt = chat(LM_PROXY, lm[0], "Reply with exactly: via-lm")
        c.ok("via-lm" in ans, f"LM-slot proxy model={lm[0]} → {ans[:20]!r} ({dt:.1f}s)")
    code, d = get(MAC_OLLAMA + "/api/tags")
    mac_ol = {m["name"] for m in d.get("models", [])} if code == 200 and isinstance(d, dict) else set()
    box_only = [m for m in models(OL_PROXY) if m not in mac_ol]
    if box_only:
        code, _, ans, dt = chat(OL_PROXY, box_only[0], "Reply with exactly: via-box")
        c.ok("via-box" in ans, f"Ollama proxy 4090-only model={box_only[0]} → {ans[:20]!r} ({dt:.1f}s)")
    shared = [m for m in models(OL_PROXY) if m in mac_ol]
    if shared:
        n = 6
        res = [None] * n

        def one(i):
            res[i] = chat(OL_PROXY, shared[0], f"Say hi {i}", max_tokens=6)[0]
        ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
        [t.start() for t in ts]; [t.join() for t in ts]
        c.ok(all(r == 200 for r in res), f"{n} concurrent requests for shared model {shared[0]}: http {res}")
    ws = ledger_wait(t0, 2 + (6 if shared else 0))
    by = {}
    for w in ws:
        by.setdefault(where(w, names), []).append(f"{w['model']}/{w['engine']}")
    c.ok(ws, f"Mac PAIR ledger since start: {json.dumps(by, ensure_ascii=False)}")
    if lm:
        c.ok(any(w["engine"] == "lmstudio" and where(w, names) != "this-mac" for w in ws),
             "LM-slot job ran on the remote node (4090 SGLang)")
    if box_only:
        c.ok(any(w["model"] == box_only[0] and where(w, names) != "this-mac" for w in ws),
             f"4090-only model {box_only[0]} ran on the remote node")
    return c.result()


def link4_decider() -> bool:
    c = Check("link 4: decider")
    code, d = get(DECIDER + "/health")
    c.ok(code == 200, f"health {d}")
    cases = [({"session": "t1", "turn": 1, "task_type": "code", "summary": "prove this race condition fix",
               "first_user_text": "prove this race condition fix"}, "strong"),
             ({"session": "t2", "turn": 1, "task_type": None, "summary": "translate hello to French",
               "first_user_text": "translate hello to French"}, "weak")]
    before = len(DECIDER_LOG.read_text().splitlines()) if DECIDER_LOG.exists() else 0
    for state, expect in cases:
        code, dec = post(DECIDER + "/decide", state, timeout=10)
        okk = code == 200 and isinstance(dec, dict) and abs(sum(dec["route"].values()) - 1) < 1e-6
        top = max(dec["route"], key=dec["route"].get) if okk else "?"
        c.ok(okk and top == expect, f"{state['first_user_text'][:28]!r} → route={ {k: round(v, 2) for k, v in dec['route'].items()} if okk else dec} "
                                    f"task_type={dec.get('task_type') if okk else '?'} reason={dec.get('reason') if okk else '?'}")
    after = len(DECIDER_LOG.read_text().splitlines()) if DECIDER_LOG.exists() else 0
    c.ok(after == before + len(cases), f"decisions logged to {DECIDER_LOG.relative_to(ROOT)} (+{after - before})")
    return c.result()


def link5_model_route() -> bool:
    c = Check("link 5: Switchyard model routes → PAIR → engine")
    names = node_names()
    t0 = time.time()
    hub_models = models(HUB)
    c.ok({"auto", "small", "large"} <= set(hub_models), f"hub routes: {hub_models}")
    for route in ("small", "large"):
        code, d, ans, dt = chat(HUB, route, f"Reply with exactly: {route}-ok")
        served = d.get("model") if isinstance(d, dict) else "?"
        c.ok(f"{route}-ok" in ans, f"model={route} → served by {served} → {ans[:20]!r} ({dt:.1f}s)")
    ws = ledger_wait(t0, 2)
    c.ok(len(ws) >= 2, "PAIR ledger: " + ", ".join(f"{w['model']}/{w['engine']}@{where(w, names)}" for w in ws))
    return c.result()


def link6_auto_route() -> bool:
    c = Check("link 6: Switchyard auto route with the decider")
    sid = "link6-" + nonce()
    t0 = time.time()
    code, st = get(HUB + "/v1/routing/stats")
    calls0 = sum(v.get("calls", 0) for v in st.get("classifier", {}).get("models", {}).values()) if code == 200 else 0
    hist = []
    served = []
    for text in (f"[{sid}] Debug this race condition in the connection pool and prove the fix is correct.",
                 "It still fails under load. Root cause, please.", "Now write the patch."):
        hist.append({"role": "user", "content": text})
        body = {"model": "auto", "messages": hist, "max_tokens": 40, **NO_THINK}
        code, d = post(HUB + "/v1/chat/completions", body, {"x-task-type": "code", "x-switchyard-session-id": sid})
        ans = ""
        if code == 200 and d.get("choices"):
            m = d["choices"][0]["message"]; ans = (m.get("content") or "").strip()
        hist.append({"role": "assistant", "content": ans})
        served.append(d.get("model") if isinstance(d, dict) else f"http {code}")
    c.ok(all(isinstance(s, str) and "http" not in s for s in served), f"3 turns served by: {served}")
    code, st = get(HUB + "/v1/routing/stats")
    calls1 = sum(v.get("calls", 0) for v in st.get("classifier", {}).get("models", {}).values()) if code == 200 else 0
    c.ok(calls1 - calls0 == 2, f"decider consulted {calls1 - calls0} times (expect 2: turns 1–2, then latched)")
    recent = [d for d in decider_tail(4) if d["state"].get("session")]
    tt = [d["state"].get("task_type") for d in recent[-2:]]
    c.ok(tt == ["code", "code"], f"decider saw x-task-type on both turns: {tt}")
    time.sleep(1)
    traces = rl_after(t0)
    tiers = [t.get("served_tier") for t in traces][-3:]
    c.ok(tiers == ["weak", "strong", "strong"], f"RL traces served_tier: {tiers} (expect weak → strong → strong)")
    with_dec = sum(1 for t in traces[-3:] if (t.get("decision") or {}).get("route"))
    c.ok(with_dec == 2, f"traces carrying the decision: {with_dec} (expect 2)")
    return c.result()


def link7_client() -> bool:
    c = Check("link 7: personal-side local service")
    code, d = get(LOCAL + "/v1/models")
    c.ok(code == 200, f"local service {LOCAL} answers /v1/models (http {code})")
    c.ok(set(models(LOCAL)) == set(models(HUB)) and models(HUB), "local /v1/models mirrors the hub")
    sid = "link7-" + nonce()
    code, d, ans, dt = chat(LOCAL, "auto", f"[{sid}] Reply with exactly: client-ok",
                            headers={"x-task-type": "extract", "x-switchyard-session-id": sid}, max_tokens=12)
    c.ok(code == 200, f"chat through the local service → {ans[:20]!r} ({dt:.1f}s)")
    last = decider_tail(1)
    seen = last[0]["state"].get("task_type") if last else None
    c.ok(seen == "extract", f"x-task-type forwarded to the hub's decider: saw {seen!r} (expect 'extract')")
    return c.result()


LINKS = {1: link1_engines, 2: link2_pair_node, 3: link3_pair_route, 4: link4_decider,
         5: link5_model_route, 6: link6_auto_route, 7: link7_client}


def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "all"
    if which == "all":
        results = {k: f() for k, f in LINKS.items()}
        print("=== whole chain: tests/e2e_chain.py")
        env = {**os.environ, "E2E_LOCAL": LOCAL, "E2E_HUB": HUB, "E2E_PAIR_RPC": os.environ.get("E2E_PAIR_RPC", "")}
        e2e = subprocess.run([sys.executable, str(ROOT / "tests" / "e2e_chain.py")], env=env).returncode == 0
        print("\n=== summary")
        for k, v in results.items():
            print(f"  link {k} {LINKS[k].__name__[6:]:<12} {'PASS' if v else 'FAIL'}")
        print(f"  whole chain       {'PASS' if e2e else 'FAIL'}")
        return 0 if all(results.values()) and e2e else 1
    return 0 if LINKS[int(which)]() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
