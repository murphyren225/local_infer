"""Tandem Gateway — OpenAI-compatible entrypoint.

Request lifecycle for POST /v1/chat/completions:

    cache lookup ─→ route decision ─→ backend call ─→ escalation check
         │                                                  │
         └── hit: return immediately          miss on small: retry on large

Every response carries ``x-tandem-lane`` / ``x-tandem-reason`` headers so
callers (and evals) can audit where each request actually ran.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .backends import LaneBackend, build_backends
from .cache import ResponseCache, cache_key, cacheable
from .config import load_config
from .escalation import should_escalate
from .metrics import Stats
from .router import decide

logger = logging.getLogger("tandem")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    app.state.cfg = cfg
    app.state.client = client
    app.state.backends = build_backends(cfg, client)
    app.state.cache = ResponseCache(
        max_entries=cfg["cache"]["max_entries"],
        ttl_seconds=cfg["cache"]["ttl_seconds"],
    )
    app.state.stats = Stats()
    logger.info("tandem gateway %s up, lanes: %s", __version__, list(app.state.backends))
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="Tandem Gateway", version=__version__, lifespan=lifespan)


PROTOCOL_VERSION = "1"  # docs/agent-interface.md


def _headers(lane: str, reason: str, session: str | None = None, **extra: str) -> dict[str, str]:
    headers = {
        "x-tandem-protocol": PROTOCOL_VERSION,
        "x-tandem-lane": lane,
        "x-tandem-reason": reason,
    }
    if session:
        headers["x-tandem-session"] = session
    headers.update(extra)
    return headers


def _agent_headers(request: Request) -> tuple[str | None, str | None]:
    """Extract (hint, session) protocol extension headers."""
    hint = request.headers.get("x-tandem-hint")
    session = request.headers.get("x-tandem-session")
    if session:
        session = session[:128]
    return hint, session


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/models")
async def models(request: Request) -> dict[str, Any]:
    backends: dict[str, LaneBackend] = request.app.state.backends
    data = [{"id": "auto", "object": "model", "owned_by": "tandem"}]
    for name, backend in backends.items():
        data.append({"id": name, "object": "model", "owned_by": "tandem"})
        data.append({"id": backend.model, "object": "model", "owned_by": "tandem"})
    return {"object": "list", "data": data}


@app.get("/admin/stats")
async def admin_stats(request: Request) -> dict[str, Any]:
    stats: Stats = request.app.state.stats
    return stats.snapshot(request.app.state.cfg["pricing_reference"])


@app.post("/admin/route_preview")
async def route_preview(request: Request) -> dict[str, Any]:
    """Return the routing decision for a payload without running inference."""
    payload = await request.json()
    hint, _ = _agent_headers(request)
    return decide(payload, request.app.state.cfg, hint).as_dict()


@app.get("/admin/sessions/{session_id}")
async def session_stats(session_id: str, request: Request):
    """Per-session ledger (protocol §2.4) — an agent task's cost, one call."""
    stats: Stats = request.app.state.stats
    snapshot = stats.session_snapshot(session_id)
    if snapshot is None:
        return JSONResponse({"error": {"message": "unknown session"}}, status_code=404)
    return {"session": session_id, **snapshot}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload: dict[str, Any] = await request.json()
    cfg = request.app.state.cfg
    stats: Stats = request.app.state.stats
    backends: dict[str, LaneBackend] = request.app.state.backends
    cache: ResponseCache = request.app.state.cache

    hint, session = _agent_headers(request)
    decision = decide(payload, cfg, hint)
    stats.record_decision(decision.reason)
    backend = backends.get(decision.lane)
    if backend is None:
        return JSONResponse(
            {"error": {"message": f"unknown lane {decision.lane!r}"}}, status_code=500
        )

    if payload.get("stream"):
        # Streamed usage isn't visible to the gateway; the session ledger
        # records the request itself (callers wanting stream token counts
        # set stream_options.include_usage and read it client-side).
        stats.record_session(session, decision.lane)
        return StreamingResponse(
            backend.stream(payload),
            media_type="text/event-stream",
            headers=_headers(decision.lane, decision.reason, session),
        )

    use_cache = cacheable(payload, cfg["cache"])
    key = cache_key(decision.lane, payload) if use_cache else None
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            stats.cache_hits += 1
            stats.record_session(session, decision.lane, cache_hit=True)
            return JSONResponse(
                cached,
                headers=_headers(
                    decision.lane, decision.reason, session, **{"x-tandem-cache": "hit"}
                ),
            )

    try:
        response = await backend.chat(payload)
    except httpx.HTTPError as exc:
        stats.lane(decision.lane).errors += 1
        logger.warning("lane %s failed: %s", decision.lane, exc)
        return JSONResponse(
            {"error": {"message": f"upstream {decision.lane} failed: {exc}"}},
            status_code=502,
        )

    escalated_reason = None
    if decision.lane == "small" and "large" in backends:
        escalated_reason = should_escalate(payload, response, cfg["escalation"])
        if escalated_reason:
            logger.info("escalating to large: %s", escalated_reason)
            stats.escalations += 1
            try:
                response = await backends["large"].chat(payload)
                decision.lane = "large"
            except httpx.HTTPError as exc:
                # keep the small answer rather than failing the request
                logger.warning("escalation failed, keeping small answer: %s", exc)
                escalated_reason = f"{escalated_reason} (retry-failed)"

    stats.record_usage(decision.lane, response.get("usage"))
    stats.record_session(
        session, decision.lane, response.get("usage"), escalated=bool(escalated_reason)
    )
    if key is not None:
        cache.put(key, response)

    extra = {"x-tandem-escalated": escalated_reason} if escalated_reason else {}
    return JSONResponse(
        response, headers=_headers(decision.lane, decision.reason, session, **extra)
    )
