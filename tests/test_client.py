"""Personal side: session derivation and the browser-origin guard (pure functions, no network)."""
import json

from client import serve


def body(*msgs):
    return json.dumps({"model": "auto", "messages": list(msgs)}).encode()


def test_session_is_stable_across_turns_of_one_conversation():
    first = body({"role": "user", "content": "fix the failing test in foo.py"})
    later = body({"role": "user", "content": "fix the failing test in foo.py"},
                 {"role": "assistant", "content": "looking"},
                 {"role": "user", "content": "it still fails"})
    assert serve.derive_session(first) == serve.derive_session(later)


def test_session_differs_between_conversations():
    a = body({"role": "user", "content": "translate this"})
    b = body({"role": "user", "content": "translate that"})
    assert serve.derive_session(a) != serve.derive_session(b)


def test_session_includes_system_prompt_and_handles_content_blocks():
    a = body({"role": "system", "content": "you are A"}, {"role": "user", "content": [{"type": "text", "text": "hi"}]})
    b = body({"role": "system", "content": "you are B"}, {"role": "user", "content": [{"type": "text", "text": "hi"}]})
    assert serve.derive_session(a) and serve.derive_session(a) != serve.derive_session(b)


def test_session_none_without_messages():
    assert serve.derive_session(None) is None
    assert serve.derive_session(b"not json") is None
    assert serve.derive_session(json.dumps({"model": "auto"}).encode()) is None


def test_origin_guard():
    ok = serve.origin_ok
    assert ok({"Host": "127.0.0.1:7000"}, 7000)                                   # CLI / Pi: no Origin
    assert ok({"Host": "localhost:7000", "Origin": "http://localhost:7000"}, 7000)  # our own page
    assert not ok({"Host": "127.0.0.1:7000", "Origin": "https://evil.example"}, 7000)
    assert not ok({"Host": "127.0.0.1:7000", "Origin": "http://127.0.0.1:7001"}, 7000)
    assert not ok({"Host": "evil.example:7000"}, 7000)                             # DNS rebinding
