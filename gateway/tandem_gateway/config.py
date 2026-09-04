"""Configuration loading: YAML overlay on top of built-in defaults."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .defaults import merged

ENV_VAR = "TANDEM_CONFIG"
SEARCH_PATHS = (
    "config/tandem.yaml",
    "/app/config/tandem.yaml",
)


def load_config(path: str | None = None) -> dict[str, Any]:
    candidate = path or os.environ.get(ENV_VAR)
    if candidate is None:
        for search in SEARCH_PATHS:
            if Path(search).is_file():
                candidate = search
                break

    overrides: dict[str, Any] = {}
    if candidate:
        import yaml  # local import: pure-logic callers never need it

        with open(candidate, "r", encoding="utf-8") as fh:
            overrides = yaml.safe_load(fh) or {}
    return merged(overrides)
