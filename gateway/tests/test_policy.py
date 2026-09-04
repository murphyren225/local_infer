from tandem_gateway.defaults import DEFAULTS
from tandem_gateway.router import decide


def payload(model, text="hi"):
    return {"model": model, "messages": [{"role": "user", "content": text}]}


def test_explicit_lane_wins_over_heuristics():
    decision = decide(payload("large", "翻译：hello"), DEFAULTS)
    assert decision.lane == "large"
    assert decision.reason == "explicit-lane"


def test_explicit_model_id_maps_to_lane():
    model_id = DEFAULTS["lanes"]["small"]["model"]
    decision = decide(payload(model_id, "分析这份合同的风险条款"), DEFAULTS)
    assert decision.lane == "small"
    assert decision.reason == "explicit-model"


def test_auto_uses_heuristics():
    decision = decide(payload("auto", "帮我总结这段话：会议改到周五"), DEFAULTS)
    assert decision.lane == "small"
    assert decision.reason.startswith("heuristic:")


def test_decision_serializes():
    decision = decide(payload("auto", "证明勾股定理，并给出三种方法的利弊分析"), DEFAULTS)
    data = decision.as_dict()
    assert data["lane"] == "large"
    assert isinstance(data["signals"], list)
