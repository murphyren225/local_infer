"""Filesystem layout. Everything mutable lives under ROOT/state and ROOT/logs."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
LOGS = ROOT / "logs"
PRESETS = ROOT / "cluster" / "inference" / "presets"
ROUTES_FILE = STATE / "routes.yaml"
MODE_FILE = STATE / "cluster_mode"
REGISTRY_FILE = STATE / "nodes.json"
TOKEN_FILE = STATE / "join_token"
MODELS_DIR = Path(os.environ.get("HOMED_MODELS", Path.home() / ".homed" / "models"))

GATEWAY_PORT = int(os.environ.get("HOMED_GATEWAY_PORT", "4000"))
CONSOLE_PORT = int(os.environ.get("HOMED_CONSOLE_PORT", "6006"))


def ensure_dirs() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)


def python_for_services() -> str:
    """Interpreter that has fastapi/uvicorn/switchyard installed."""
    venv = Path(os.environ.get("HOMED_VENV", Path.home() / ".homed" / "venv"))
    candidate = venv / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def switchyard_bin() -> str:
    venv = Path(os.environ.get("HOMED_VENV", Path.home() / ".homed" / "venv"))
    candidate = venv / "bin" / "switchyard"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("switchyard")
    if not found:
        raise RuntimeError("switchyard not found: pip install nemo-switchyard (Python >= 3.12)")
    return found
