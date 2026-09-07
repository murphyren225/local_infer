"""Node registry — the data structure behind hot-plug (design.md part 1 §4).

state/nodes.json:
{
  "nodes": {
    "<name>": {"pool": "strong|weak|long", "model": "<served name>",
               "base_url": "http://host:port/v1", "hw": "<label>",
               "engine": "vllm|llama.cpp|remote", "local": true|false}
  }
}

Adding a node = one register() call; the router regenerates from this file.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass

from ..paths import REGISTRY_FILE, TOKEN_FILE, ensure_dirs

POOLS = ("strong", "weak", "long")


@dataclass
class Node:
    name: str
    pool: str
    model: str
    base_url: str
    hw: str = ""
    engine: str = "vllm"
    local: bool = True

    @property
    def health_url(self) -> str:
        return self.base_url.rsplit("/v1", 1)[0] + "/health"


def load() -> dict[str, Node]:
    if not REGISTRY_FILE.exists():
        return {}
    raw = json.loads(REGISTRY_FILE.read_text())
    return {k: Node(**v) for k, v in raw.get("nodes", {}).items()}


def save(nodes: dict[str, Node]) -> None:
    ensure_dirs()
    REGISTRY_FILE.write_text(json.dumps({"nodes": {k: asdict(v) for k, v in nodes.items()}}, indent=2, ensure_ascii=False))


def register(node: Node) -> None:
    if node.pool not in POOLS:
        raise ValueError(f"pool must be one of {POOLS}")
    nodes = load()
    nodes[node.name] = node
    save(nodes)


def unregister(name: str) -> bool:
    nodes = load()
    if name not in nodes:
        return False
    del nodes[name]
    save(nodes)
    return True


def by_pool(nodes: dict[str, Node]) -> dict[str, list[Node]]:
    out: dict[str, list[Node]] = {p: [] for p in POOLS}
    for n in nodes.values():
        out.setdefault(n.pool, []).append(n)
    return out


def issue_token() -> str:
    ensure_dirs()
    tok = "JOIN-" + secrets.token_hex(4).upper()
    TOKEN_FILE.write_text(tok)
    return tok


def check_token(tok: str) -> bool:
    return TOKEN_FILE.exists() and secrets.compare_digest(TOKEN_FILE.read_text().strip(), tok.strip())
