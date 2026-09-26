"""The decision service. Run: python3 -m decider [--backend rules|anyjev|jev] [--port 4100]

Switchyard (fork) posts a DecisionState here and expects a Decision back; the shapes are
copied verbatim from switchyard/lib/decision/provider.py so this service has no import
dependency on Switchyard. Runs with the standard library only for the rules backend.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TIERS = ("weak", "strong", "cloud")
TASK_TYPES = ("code", "edit", "qa", "extract", "agent")
LOG = Path(os.environ.get("DECIDER_LOG", Path(__file__).resolve().parents[1] / "logs" / "decisions.jsonl"))


def normalize(route: dict) -> dict:
    probs = {t: max(0.0, float(route.get(t, 0.0))) for t in TIERS}
    total = sum(probs.values()) or 1.0
    return {t: p / total for t, p in probs.items()}


# ---------------------------------------------------------------- backends

class RulesBackend:
    """No model. Task type from the header or from keywords; tier from task type + length."""
    name = "rules"
    STRONG_HINTS = ("prove", "debug", "root cause", "race condition", "design", "architecture", "refactor",
                    "证明", "分析", "架构", "评审", "调试", "根因")
    TYPE_HINTS = {"code": ("code", "script", "function", "bug", "代码", "脚本", "函数"),
                  "edit": ("rewrite", "rename", "reformat", "translate", "改写", "翻译", "润色"),
                  "extract": ("extract", "parse", "pull out", "提取", "抽取"),
                  "agent": ("run", "execute", "folder", "files", "shell", "执行", "目录", "文件")}

    def decide(self, state: dict) -> dict:
        text = (state.get("first_user_text") or "") + " " + (state.get("summary") or "")
        low = text.lower()
        task_type = state.get("task_type")
        if not task_type:
            task_type = next((t for t, hints in self.TYPE_HINTS.items() if any(h in low for h in hints)), "qa")
        strong = 0.15
        reason = "default weak"
        if any(h in low for h in self.STRONG_HINTS):
            strong, reason = 0.7, "strong hint"
        if task_type in ("code", "agent") and strong < 0.5:
            strong, reason = 0.5, f"task_type={task_type}"
        if len(state.get("summary") or "") > 6000:
            strong, reason = max(strong, 0.7), "long transcript"
        rewrite = 0.6 if len(state.get("first_user_text") or "") > 300 or " um " in low or "像" in low else 0.1
        return {"route": normalize({"weak": 1 - strong, "strong": strong}), "confidence": 0.5,
                "task_type": task_type, "rewrite": rewrite, "reason": reason, "provider": self.name}


class JevBackend:
    """Forward to a TypeSafe-compatible /v1/systemone server (Jev, Kev, Decider, AnyJev server)."""
    name = "jev"

    def __init__(self, base_url: str, api_key: str, model: str = "jev-latest") -> None:
        self.url = base_url.rstrip("/") + "/v1/systemone"; self.key = api_key; self.model = model

    def decide(self, state: dict) -> dict:
        body = {"model": self.model, "state": state.get("summary", ""), "questions": {
            "route": {"type": "choice", "instructions": "Which model tier should serve the next turn?",
                      "criteria": {"weak": "simple or mechanical; a small local model handles it",
                                   "strong": "needs reasoning, debugging, design or careful multi-step work",
                                   "cloud": "needs very long context or knowledge a local model lacks"}},
            "task_type": {"type": "choice", "instructions": "What kind of task is this?",
                          "criteria": {"code": "write or fix code", "edit": "rewrite or reformat text", "qa": "answer a question",
                                       "extract": "pull structured data out of text", "agent": "multi-step task using tools"}},
            "rewrite": {"type": "noul", "instructions": "Would rewriting the request into a clearer, structured instruction help?"}}}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ans = json.load(r)
        ans = ans.get("answers", ans)
        route = ans["route"].get("probabilities") or ans["route"].get("distribution") or {ans["route"].get("choice", "weak"): 1.0}
        return {"route": normalize(route), "confidence": float(ans["route"].get("confidence", 1.0)),
                "task_type": ans.get("task_type", {}).get("choice"), "rewrite": ans.get("rewrite", {}).get("noul"),
                "reason": "jev", "provider": self.name}


class AnyJevBackend:
    """AnyJev over a local Qwen3 (needs `pip install anyjev[hf]` and a GPU). Lazy import."""
    name = "anyjev"

    def __init__(self, model: str) -> None:
        from anyjev import Decider, Question  # type: ignore
        from anyjev.backends.hf import HFBackend  # type: ignore
        self.q = Question
        self.d = Decider(HFBackend(model), level="L0")

    def decide(self, state: dict) -> dict:
        qs = [self.q.choice("Which model tier should serve the next turn: weak (simple), strong (needs reasoning), cloud (needs huge context)?",
                            list(TIERS), name="route"),
              self.q.choice("What kind of task is this?", list(TASK_TYPES), name="task_type")]
        out = self.d.decide(state.get("summary", ""), qs)
        route = dict(out["route"].distribution)
        tt = out["task_type"]
        return {"route": normalize(route), "confidence": float(getattr(out["route"], "confidence", max(route.values()))),
                "task_type": max(tt.distribution, key=tt.distribution.get), "reason": "anyjev", "provider": self.name}


def build(args) -> object:
    if args.backend == "rules":
        return RulesBackend()
    if args.backend == "jev":
        return JevBackend(args.jev_url, os.environ.get("TYPESAFE_API_KEY", ""), args.jev_model)
    if args.backend == "anyjev":
        return AnyJevBackend(args.model)
    raise SystemExit(f"unknown backend {args.backend}")


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    backend: object = RulesBackend()
    fallback = RulesBackend()

    def log_message(self, *a): pass

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "backend": getattr(self.backend, "name", "?")})
        self._json(404, {})

    def do_POST(self):
        if self.path != "/decide":
            return self._json(404, {})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            state = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        t0 = time.monotonic()
        try:
            decision = self.backend.decide(state)  # type: ignore[attr-defined]
        except Exception as exc:  # the floor never fails; Switchyard would fail open anyway
            decision = self.fallback.decide(state)
            decision["reason"] = f"fallback after {type(exc).__name__}: {exc}"[:200]
        decision["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a") as f:
                f.write(json.dumps({"t": time.time(), "state": state, "decision": decision}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self._json(200, decision)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="decider", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", default=os.environ.get("DECIDER_BACKEND", "rules"), choices=["rules", "anyjev", "jev"])
    p.add_argument("--port", type=int, default=int(os.environ.get("DECIDER_PORT", "4100")))
    p.add_argument("--model", default="Qwen/Qwen3-1.7B", help="anyjev: HF model id or local path")
    p.add_argument("--jev-url", default="https://openrouter.ai/api")
    p.add_argument("--jev-model", default="typesafe/jev-1.13")
    args = p.parse_args(argv)
    Handler.backend = build(args)
    print(f"decider backend={args.backend} on 127.0.0.1:{args.port}  log={LOG}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0
