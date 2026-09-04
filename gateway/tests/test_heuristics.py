from tandem_gateway.defaults import DEFAULTS
from tandem_gateway.router.heuristics import assess

ROUTING = DEFAULTS["routing"]


def payload(text, **kwargs):
    body = {"model": "auto", "messages": [{"role": "user", "content": text}]}
    body.update(kwargs)
    return body


def test_short_simple_goes_small():
    result = assess(payload("帮我把这句话翻译成英文：今天天气不错"), ROUTING)
    assert result.lane == "small"


def test_tools_force_large():
    result = assess(payload("现在几点", tools=[{"type": "function"}]), ROUTING)
    assert result.lane == "large"
    assert result.signals[0].name == "tools"


def test_long_context_forces_large():
    result = assess(payload("总结一下：" + "x" * 7000), ROUTING)
    assert result.lane == "large"
    assert result.signals[0].name == "long-context"


def test_reasoning_with_code_goes_large():
    text = "分析一下这段代码为什么内存泄漏\n```python\nwhile True: pass\n```"
    result = assess(payload(text), ROUTING)
    assert result.lane == "large"
    assert result.score >= ROUTING["large_threshold"]


def test_multimodal_content_parts_are_flattened():
    body = {
        "model": "auto",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "把这段话润色一下：你好世界"}],
            }
        ],
    }
    result = assess(body, ROUTING)
    assert result.lane == "small"


def test_every_decision_is_explainable():
    result = assess(payload("为什么分布式系统需要共识算法？请深入分析利弊。"), ROUTING)
    assert result.reason
    assert all(s.name for s in result.signals)
