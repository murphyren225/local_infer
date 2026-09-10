"""Unit tests for the control plane and router modules (no GPU, no network)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cluster.control import health, registry            # noqa: E402
from cluster.control.registry import Node               # noqa: E402
from cluster.node import presets, probe                 # noqa: E402
from cluster.router import routes                       # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_FILE", tmp_path / "nodes.json")
    monkeypatch.setattr(registry, "TOKEN_FILE", tmp_path / "join_token")
    monkeypatch.setattr(routes, "ROUTES_FILE", tmp_path / "routes.yaml")
    monkeypatch.setattr(routes, "MODE_FILE", tmp_path / "cluster_mode")
    monkeypatch.setattr(registry, "ensure_dirs", lambda: None)
    monkeypatch.setattr(routes, "ensure_dirs", lambda: None)
    return tmp_path


def node(name, pool, model, port):
    return Node(name, pool, model, f"http://127.0.0.1:{port}/v1", hw="x", engine="vllm", local=True)


def assessment(strong=True, weak=True):
    a = health.Assessment()
    s, w = node("gpu", "strong", "qwen3-32b-awq", 8001), node("mac", "weak", "qwen3-1.7b-gguf", 8002)
    a.healthy["strong"] = [s] if strong else []
    a.healthy["weak"] = [w] if weak else []
    a.healthy["long"] = []
    a.dead = [n for n, ok in ((s, strong), (w, weak)) if not ok]
    return a


def test_registry_roundtrip(state):
    registry.register(node("gpu", "strong", "m", 8001))
    registry.register(node("mac", "weak", "m2", 8002))
    assert set(registry.load()) == {"gpu", "mac"}
    assert registry.unregister("mac") and "mac" not in registry.load()
    tok = registry.issue_token()
    assert tok.startswith("JOIN-") and registry.check_token(tok) and not registry.check_token("nope")


def test_registry_rejects_unknown_pool(state):
    with pytest.raises(ValueError):
        registry.register(Node("x", "gpu", "m", "http://h/v1"))


@pytest.mark.parametrize("strong,weak,cloud,expected", [
    (True, True, False, "normal"),
    (False, True, False, "degraded-large-small"),
    (False, True, True, "degraded-large-cloud"),
    (True, False, False, "degraded-small-large"),
    (True, False, True, "degraded-small-cloud"),
    (False, False, True, "degraded-all-cloud"),
    (False, False, False, "dead"),
])
def test_mode_matrix(strong, weak, cloud, expected):
    assert health.mode(assessment(strong, weak), cloud) == expected


def test_routes_normal_is_escalation(state):
    text, m = routes.render(assessment(), routes.Cloud(False))
    assert m == "normal"
    assert "type: escalation_router" in text
    assert "fallback_target_on_evict: strong" in text
    assert text.count("type: model") == 3            # small, large, cloud(fake)


def test_routes_degraded_points_to_survivor(state):
    text, m = routes.render(assessment(strong=False), routes.Cloud(False))
    assert m == "degraded-large-small"
    assert "escalation_router" not in text
    assert "base_url: http://127.0.0.1:8002/v1" in text and "8001" not in text


def test_routes_degraded_with_cloud(state):
    cloud = routes.Cloud(True, "big-cloud-model", "https://api.example/v1", "sk")
    text, m = routes.render(assessment(strong=False), cloud)
    assert m == "degraded-large-cloud"
    assert "model: big-cloud-model" in text and "api_key: sk" in text


def test_routes_all_dead_without_cloud_raises(state):
    with pytest.raises(RuntimeError):
        routes.render(assessment(False, False), routes.Cloud(False))


def test_routes_write_records_mode(state):
    m = routes.write(assessment(), routes.Cloud(False))
    assert m == "normal"
    assert (state / "routes.yaml").read_text().startswith("# generated")
    assert (state / "cluster_mode").read_text() == "normal"


def test_preset_qwen3_24gb_parses():
    lanes = presets.load("qwen3-24gb")
    by = {l.pool: l for l in lanes}
    assert by["strong"].name == "qwen3-32b-awq" and by["strong"].engine == "vllm"
    assert "--tool-call-parser" in by["strong"].args and "hermes" in by["strong"].args
    assert by["weak"].port == 8002


def test_preset_cpu_parses():
    lanes = presets.load("cpu-1.7b")
    assert len(lanes) == 1 and lanes[0].engine == "llama.cpp" and lanes[0].pool == "weak"
    assert lanes[0].model_path.endswith("Qwen3-1.7B-Q8_0.gguf")


@pytest.mark.parametrize("kind,vram,expected", [
    ("nvidia", 24564, "qwen3-24gb"), ("nvidia", 16000, "qwen3-16gb"),
    ("nvidia", 12000, "qwen3-12gb"), ("cpu", 0, "cpu-1.7b"), ("apple", 0, "cpu-1.7b"),
])
def test_choose_preset(kind, vram, expected):
    assert probe.choose_preset(probe.Hardware(kind, "x", vram_mib=vram)) == expected


def test_preset_args_keep_json_quotes():
    """vLLM's --default-chat-template-kwargs needs a JSON token; shlex must not eat the quotes."""
    from cluster.node import presets
    lanes = presets.load("qwen3-24gb")
    for lane in lanes:
        i = lane.args.index("--default-chat-template-kwargs")
        assert lane.args[i + 1] == '{"enable_thinking":false}'
