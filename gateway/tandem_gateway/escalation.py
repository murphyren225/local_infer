"""Escalation — Tier 3 of the routing policy (answer-quality backstop).

After the small lane answers a non-streaming request, cheap checks decide
whether to silently retry on the large lane. This converts routing
mistakes from "user gets a bad answer" into "user waits a bit longer",
which is the right trade for a gateway that defaults to the small lane.

Streaming requests cannot be escalated (bytes already left the building),
so for streams the pre-generation decision is final.
"""
from __future__ import annotations

import json
from typing import Any


def _first_content(response: dict[str, Any]) -> str | None:
    choices = response.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


def should_escalate(
    payload: dict[str, Any],
    response: dict[str, Any],
    escalation_cfg: dict[str, Any],
) -> str | None:
    """Return an escalation reason, or None if the answer stands."""
    if not escalation_cfg.get("enabled", True):
        return None

    content = _first_content(response)
    if content is None or not content.strip():
        return "empty-output"

    response_format = payload.get("response_format") or {}
    if response_format.get("type") == "json_object":
        try:
            json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return "invalid-json"

    lowered = content.lower()
    for pattern in escalation_cfg.get("uncertainty_patterns", []):
        if pattern.lower() in lowered:
            return "self-uncertain"

    return None
