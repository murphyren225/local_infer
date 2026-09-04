#!/usr/bin/env python3
"""Offline routing eval — the quality gate for the router.

Runs the routing policy (no GPU, no network, no third-party deps) against
a labeled dataset and fails if accuracy drops below the threshold. CI runs
this on every push: you cannot merge a heuristics change that silently
breaks routing.

Usage:
    python3 evals/run_evals.py [--dataset evals/datasets/routing_eval.jsonl]
                               [--min-accuracy 0.9] [--verbose]

Dataset format (JSONL), one case per line:
    {"prompt": "...", "expect": "small|large", "tag": "translate",
     "extra": {...payload fields...}, "repeat": 1}
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

from tandem_gateway.defaults import merged  # noqa: E402
from tandem_gateway.router import decide  # noqa: E402


def load_cases(path: Path) -> list[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            case["_line"] = line_no
            cases.append(case)
    return cases


def build_payload(case: dict) -> dict:
    prompt = case["prompt"] * int(case.get("repeat", 1))
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
    }
    payload.update(case.get("extra", {}))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", default=str(ROOT / "evals/datasets/routing_eval.jsonl")
    )
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cfg = merged(None)
    cases = load_cases(Path(args.dataset))
    per_tag: dict[str, list[bool]] = defaultdict(list)
    failures = []

    for case in cases:
        decision = decide(build_payload(case), cfg)
        correct = decision.lane == case["expect"]
        per_tag[case.get("tag", "untagged")].append(correct)
        if not correct:
            failures.append((case, decision))
        if args.verbose:
            mark = "ok " if correct else "MISS"
            print(
                f"[{mark}] line {case['_line']:>3} expect={case['expect']:<5} "
                f"got={decision.lane:<5} score={decision.score:<3} {decision.reason}"
            )

    total = len(cases)
    correct_count = total - len(failures)
    accuracy = correct_count / total if total else 0.0

    print(f"\nrouting accuracy: {correct_count}/{total} = {accuracy:.1%}")
    for tag in sorted(per_tag):
        results = per_tag[tag]
        print(f"  {tag:<14} {sum(results)}/{len(results)}")

    if failures:
        print("\nmisrouted cases:")
        for case, decision in failures:
            snippet = case["prompt"][:60].replace("\n", " ")
            print(
                f"  line {case['_line']}: expect={case['expect']} got={decision.lane} "
                f"score={decision.score} reason={decision.reason} | {snippet}"
            )

    if accuracy < args.min_accuracy:
        print(f"\nFAIL: accuracy {accuracy:.1%} < gate {args.min_accuracy:.1%}")
        return 1
    print(f"\nPASS: accuracy {accuracy:.1%} >= gate {args.min_accuracy:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
