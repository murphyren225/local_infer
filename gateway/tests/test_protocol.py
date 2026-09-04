"""Tests for the agent protocol v1 (docs/agent-interface.md)."""
from tandem_gateway.defaults import DEFAULTS
from tandem_gateway.metrics import MAX_SESSIONS, Stats
from tandem_gateway.router import decide


def payload(text, **kwargs):
    body = {"model": "auto", "messages": [{"role": "user", "content": text}]}
    body.update(kwargs)
    return body


HARD_PROMPT = "请深入分析微服务和单体架构的利弊，并给出技术选型建议"


def test_hint_overrides_heuristics():
    # heuristics alone would route this to large…
    assert decide(payload(HARD_PROMPT), DEFAULTS).lane == "large"
    # …but the agent says it's a digest step
    decision = decide(payload(HARD_PROMPT), DEFAULTS, hint="digest")
    assert decision.lane == "small"
    assert decision.reason == "hint:digest"


def test_hard_rule_beats_hint():
    decision = decide(
        payload("总结一下", tools=[{"type": "function"}]), DEFAULTS, hint="digest"
    )
    assert decision.lane == "large"
    assert decision.reason == "hint-overridden:tools"

    decision = decide(payload("总结:" + "x" * 7000), DEFAULTS, hint="chat")
    assert decision.lane == "large"
    assert decision.reason == "hint-overridden:long-context"


def test_unknown_hint_falls_back_to_heuristics():
    decision = decide(payload(HARD_PROMPT), DEFAULTS, hint="v2-new-step-type")
    assert decision.lane == "large"
    assert decision.reason.startswith("heuristic:")


def test_explicit_model_beats_hint():
    decision = decide(payload(HARD_PROMPT, model="small"), DEFAULTS, hint="plan")
    assert decision.lane == "small"
    assert decision.reason == "explicit-lane"


def test_agreeing_hint_and_hard_rule():
    decision = decide(
        payload("规划步骤", tools=[{"type": "function"}]), DEFAULTS, hint="plan"
    )
    assert decision.lane == "large"
    assert decision.reason == "hint:plan"


def test_session_ledger_aggregates():
    stats = Stats()
    stats.record_session("task-1", "small", {"prompt_tokens": 10, "completion_tokens": 5})
    stats.record_session("task-1", "large", {"prompt_tokens": 100, "completion_tokens": 50})
    stats.record_session("task-1", "small", cache_hit=True)
    stats.record_session("task-1", "large", escalated=True)
    stats.record_session(None, "small")  # no session header → not recorded

    snap = stats.session_snapshot("task-1")
    assert snap["requests"] == 4
    assert snap["lanes"] == {"small": 2, "large": 2}
    assert snap["prompt_tokens"] == 110
    assert snap["completion_tokens"] == 55
    assert snap["cache_hits"] == 1
    assert snap["escalations"] == 1
    assert stats.session_snapshot("unknown") is None
    assert stats.snapshot({})["active_sessions"] == 1


def test_session_lru_cap():
    stats = Stats()
    for i in range(MAX_SESSIONS + 10):
        stats.record_session(f"s{i}", "small")
    assert len(stats.sessions) == MAX_SESSIONS
    assert stats.session_snapshot("s0") is None          # oldest evicted
    assert stats.session_snapshot(f"s{MAX_SESSIONS + 9}") is not None
