"""Built-in default configuration.

Every knob here can be overridden by config/tandem.yaml. Keeping the full
default tree in code means the pure-logic modules (router, evals, tests)
work with zero external dependencies — no YAML parser required.
"""
from __future__ import annotations

import copy
from typing import Any

DEFAULTS: dict[str, Any] = {
    "gateway": {
        "host": "0.0.0.0",
        "port": 8080,
    },
    "lanes": {
        "small": {
            "base_url": "http://vllm-small:8000/v1",
            "model": "Qwen/Qwen3-1.7B-FP8",
            "revision": "main",
        },
        "large": {
            "base_url": "http://vllm-large:8000/v1",
            "model": "Qwen/Qwen3-32B-AWQ",
            "revision": "main",
        },
    },
    "routing": {
        # score >= large_threshold  =>  route to the large lane
        "large_threshold": 3,
        # prompts longer than this always go to the large lane
        # (small models degrade fastest on long context)
        "max_small_prompt_chars": 6000,
        # requests that declare tools/functions always go large
        "force_large_on_tools": True,
        "extra_large_keywords": [],
        "extra_small_keywords": [],
    },
    "cache": {
        "enabled": True,
        "max_entries": 10000,
        "ttl_seconds": 3600,
        # only cache near-deterministic requests
        "max_temperature": 0.2,
    },
    "escalation": {
        "enabled": True,
        "uncertainty_patterns": [
            "我不确定",
            "我无法确定",
            "无法回答这个问题",
            "i'm not sure",
            "i am not sure",
            "i cannot answer",
        ],
    },
    # Reference cloud prices (USD per 1M tokens) used ONLY to estimate
    # "what this traffic would have cost on a cloud API". Not billing.
    "pricing_reference": {
        "input_per_m": 3.0,
        "output_per_m": 15.0,
    },
}


def merged(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge *overrides* on top of DEFAULTS and return a new dict."""
    base = copy.deepcopy(DEFAULTS)
    if overrides:
        _deep_merge(base, overrides)
    return base


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
