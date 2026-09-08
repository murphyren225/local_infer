"""Process management: detached spawn, pid files, HTTP health waits."""
from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.error
import urllib.request

from ..paths import LOGS, ensure_dirs


def healthy(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_http(url: str, tries: int, interval: float = 5.0) -> bool:
    for _ in range(tries):
        if healthy(url):
            return True
        time.sleep(interval)
    return healthy(url)


def pid_path(name: str):
    return LOGS / f"{name}.pid"


def spawn(name: str, cmd: list[str], env: dict | None = None) -> int:
    """Start a detached process, log to logs/<name>.log, record pid."""
    ensure_dirs()
    log = open(LOGS / f"{name}.log", "ab")
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, **(env or {})},
        start_new_session=True,
    )
    pid_path(name).write_text(str(proc.pid))
    return proc.pid


def alive(name: str) -> bool:
    p = pid_path(name)
    if not p.exists():
        return False
    try:
        os.kill(int(p.read_text().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def stop(name: str, grace: float = 3.0) -> bool:
    p = pid_path(name)
    if not p.exists():
        return False
    try:
        pid = int(p.read_text().strip())
    except ValueError:
        p.unlink(missing_ok=True)
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            p.unlink(missing_ok=True)
            return False
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.2)
        except OSError:
            break
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
    p.unlink(missing_ok=True)
    return True
