from tandem_gateway.defaults import DEFAULTS
from tandem_gateway.escalation import should_escalate

CFG = DEFAULTS["escalation"]


def response(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_good_answer_stands():
    assert should_escalate({}, response("巴黎是法国的首都。"), CFG) is None


def test_empty_output_escalates():
    assert should_escalate({}, response("   "), CFG) == "empty-output"
    assert should_escalate({}, {"choices": []}, CFG) == "empty-output"


def test_invalid_json_escalates():
    payload = {"response_format": {"type": "json_object"}}
    assert should_escalate(payload, response("not json at all"), CFG) == "invalid-json"
    assert should_escalate(payload, response('{"ok": true}'), CFG) is None


def test_uncertainty_escalates():
    assert should_escalate({}, response("抱歉，我不确定这个问题的答案。"), CFG) == "self-uncertain"
    assert should_escalate({}, response("Honestly, I'm not sure."), CFG) == "self-uncertain"


def test_disabled_never_escalates():
    cfg = dict(CFG, enabled=False)
    assert should_escalate({}, response(""), cfg) is None
