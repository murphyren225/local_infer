"""Difficulty heuristics — Tier 1 of the routing policy.

Cheap, deterministic, explainable signals computed from the request alone
(no model call). Each signal contributes a weight; the summed score is
compared against ``routing.large_threshold``. Every decision carries its
signal list so routing behaviour can be audited and evaluated offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- signal patterns ---------------------------------------------------------

CODE_FENCE = re.compile(r"```")
MATH_HINT = re.compile(
    r"(∫|∑|√|\\frac|\\int|\\sum|证明|积分|微分|方程组|不等式|"
    r"solve for|derivative|integral|theorem|prove that)",
    re.IGNORECASE,
)

# Tasks that demand multi-step reasoning, judgement or domain depth.
LARGE_KEYWORDS = (
    "为什么", "分析", "推理", "论证", "证明", "评审", "审计", "重构",
    "架构", "设计方案", "技术选型", "根因", "风险", "合同", "条款",
    "法律", "诊断", "排查", "复盘", "权衡", "利弊", "深入",
    "why does", "analyze", "analyse", "prove", "root cause",
    "trade-off", "tradeoff", "architecture", "refactor", "audit",
    "diagnose", "step by step", "in depth", "pros and cons",
    "review this",
)

# High-volume, low-difficulty tasks a 4B model handles well.
SMALL_KEYWORDS = (
    "翻译", "总结", "摘要", "改写", "润色", "错别字", "纠错", "分类",
    "提取", "格式化", "转换成", "扩写", "缩写", "起个", "取个",
    "translate", "summarize", "summarise", "paraphrase", "proofread",
    "fix typo", "classify", "extract", "reformat", "rewrite this",
    "is this spam", "tl;dr",
)


@dataclass
class Signal:
    name: str
    weight: int
    detail: str = ""


@dataclass
class Assessment:
    lane: str
    score: int
    signals: list[Signal] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.signals:
            return "no-signals"
        return ",".join(s.name for s in self.signals)


# --- helpers -----------------------------------------------------------------

def _flatten_content(content: Any) -> str:
    """OpenAI message content may be a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "\n".join(parts)
    return ""


def _user_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        _flatten_content(m.get("content"))
        for m in messages
        if m.get("role") == "user"
    )


# --- main entry --------------------------------------------------------------

def assess(payload: dict[str, Any], routing_cfg: dict[str, Any]) -> Assessment:
    """Score one chat-completions payload and pick a lane."""
    messages: list[dict[str, Any]] = payload.get("messages") or []
    text = _user_text(messages)
    lowered = text.lower()
    signals: list[Signal] = []

    # Hard rules first: things the small model should never attempt.
    if routing_cfg.get("force_large_on_tools", True) and (
        payload.get("tools") or payload.get("functions")
    ):
        signals.append(Signal("tools", 99, "request declares tools"))
        return Assessment("large", 99, signals)

    max_chars = int(routing_cfg.get("max_small_prompt_chars", 6000))
    if len(text) > max_chars:
        signals.append(Signal("long-context", 99, f"{len(text)} chars > {max_chars}"))
        return Assessment("large", 99, signals)

    # Soft signals.
    if CODE_FENCE.search(text):
        signals.append(Signal("code", 2))
    if MATH_HINT.search(text):
        signals.append(Signal("math", 2))

    large_kw = LARGE_KEYWORDS + tuple(routing_cfg.get("extra_large_keywords", []))
    hits = [kw for kw in large_kw if kw in lowered]
    if hits:
        # One reasoning keyword alone shouldn't send a request to the
        # large lane; converging signals should. 1 hit=2, 2 hits=3, 3+=4.
        weight = 2 + min(len(hits) - 1, 2)
        signals.append(Signal("hard-task", weight, ",".join(hits[:4])))

    small_kw = SMALL_KEYWORDS + tuple(routing_cfg.get("extra_small_keywords", []))
    easy_hits = [kw for kw in small_kw if kw in lowered]
    if easy_hits:
        signals.append(Signal("easy-task", -2, ",".join(easy_hits[:3])))

    question_marks = text.count("?") + text.count("？")
    if question_marks >= 3:
        signals.append(Signal("multi-question", 1, f"{question_marks} questions"))

    if len(text) > 2000:
        signals.append(Signal("longish", 1, f"{len(text)} chars"))
    elif len(text) < 200 and not hits:
        signals.append(Signal("short", -1, f"{len(text)} chars"))

    if len(messages) > 8:
        signals.append(Signal("long-dialog", 1, f"{len(messages)} messages"))

    score = sum(s.weight for s in signals)
    threshold = int(routing_cfg.get("large_threshold", 3))
    lane = "large" if score >= threshold else "small"
    return Assessment(lane, score, signals)
