#!/usr/bin/env python3
"""Throughput / latency benchmark for a Tandem lane (or any OpenAI endpoint).

    python3 scripts/bench.py --url http://127.0.0.1:8080/v1 --model large \
        --n 16 --concurrency 4 --max-tokens 128

Reports per-request latency (avg/p50/p95), time-to-first-token (streaming),
and aggregate completion tokens/s.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx

PROMPT = "用三句话介绍一下量子计算的基本原理。"


async def one_request(client: httpx.AsyncClient, args, results: list[dict]) -> None:
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    start = time.monotonic()
    ttft = None
    completion_tokens = 0
    async with client.stream("POST", f"{args.url}/chat/completions", json=payload) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[len("data: "):])
            if ttft is None and chunk.get("choices"):
                delta = chunk["choices"][0].get("delta", {})
                if delta.get("content") or delta.get("reasoning_content"):
                    ttft = time.monotonic() - start
            usage = chunk.get("usage")
            if usage:
                completion_tokens = usage.get("completion_tokens", 0)
    results.append(
        {
            "latency": time.monotonic() - start,
            "ttft": ttft,
            "completion_tokens": completion_tokens,
        }
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--n", type=int, default=16)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()

    results: list[dict] = []
    semaphore = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(timeout=600.0) as client:
        async def bounded() -> None:
            async with semaphore:
                await one_request(client, args, results)

        wall_start = time.monotonic()
        await asyncio.gather(*(bounded() for _ in range(args.n)))
        wall = time.monotonic() - wall_start

    latencies = sorted(r["latency"] for r in results)
    ttfts = sorted(r["ttft"] for r in results if r["ttft"] is not None)
    total_tokens = sum(r["completion_tokens"] for r in results)

    def pct(values: list[float], p: float) -> float:
        return values[min(len(values) - 1, int(len(values) * p))]

    print(f"model={args.model} n={args.n} concurrency={args.concurrency} "
          f"max_tokens={args.max_tokens}")
    print(f"  wall time          {wall:.1f}s")
    print(f"  latency avg/p50/p95  "
          f"{statistics.mean(latencies):.2f} / {pct(latencies, 0.5):.2f} / "
          f"{pct(latencies, 0.95):.2f} s")
    if ttfts:
        print(f"  ttft    avg/p50/p95  "
              f"{statistics.mean(ttfts):.2f} / {pct(ttfts, 0.5):.2f} / "
              f"{pct(ttfts, 0.95):.2f} s")
    print(f"  completion tokens  {total_tokens} ({total_tokens / wall:.1f} tok/s aggregate)")


if __name__ == "__main__":
    asyncio.run(main())
