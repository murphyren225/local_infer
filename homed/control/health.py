"""Health assessment over the registry: which nodes answer /health right now."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..util.procs import healthy
from .registry import Node, by_pool


@dataclass
class Assessment:
    healthy: dict[str, list[Node]] = field(default_factory=dict)   # pool -> live nodes
    dead: list[Node] = field(default_factory=list)

    def first(self, pool: str) -> Node | None:
        lst = self.healthy.get(pool) or []
        return lst[0] if lst else None

    def ok(self, pool: str) -> bool:
        return bool(self.healthy.get(pool))


def assess(nodes: dict[str, Node]) -> Assessment:
    a = Assessment()
    for pool, lst in by_pool(nodes).items():
        a.healthy[pool] = []
        for n in lst:
            if healthy(n.health_url):
                a.healthy[pool].append(n)
            else:
                a.dead.append(n)
    return a


def mode(a: Assessment, has_cloud: bool) -> str:
    """cluster_mode enum (design.md part 3 §3.2)."""
    strong, weak = a.ok("strong"), a.ok("weak")
    if strong and weak:
        return "normal"
    if not strong and not weak:
        return "degraded-all-cloud" if has_cloud else "dead"
    if not strong:
        return "degraded-large-cloud" if has_cloud else "degraded-large-small"
    return "degraded-small-cloud" if has_cloud else "degraded-small-large"
