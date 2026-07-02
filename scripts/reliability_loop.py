#!/usr/bin/env python3
"""Reliability harness (leg 05).

Turns "~75%" into a reproducible number and proves the proxy lifts it. Runs a
sustained, context-growing, tool-using loop against a target endpoint and scores
each response *usable* vs *failed* using the proxy's own
``resilience.validate_response`` (imported, not reimplemented). Emits a
reliability percentage and a failure-reason histogram.

Targets:
* ``direct`` - the tower's ``/v1`` with no ``num_ctx`` (the opencode/crush shape).
* ``proxy``  - the local proxy's logical ``fast-think`` (num_ctx injected).

Usage::

    TOWER=<host> uv run python scripts/reliability_loop.py --target proxy --turns 6
    TOWER=<host> uv run python scripts/reliability_loop.py --target direct --turns 6
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter

from _endpoints import proxy_base_url, tower_base_url

from app.resilience import validate_response
from app.upstream import UpstreamResult

# A file-shaped blob so each accumulated tool output pushes context upward. About
# ~8k tokens per blob; a couple of these already overflow the 32k default.
_BLOB = (
    "\n".join(
        f"    row[{i}] = compute(payload[{i}], config['key_{i}'], flags={i % 7})  # noqa"
        for i in range(1200)
    )
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_line_count",
            "description": "Return how many lines are in the accumulated file context.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }
]

_SYSTEM = (
    "You are a coding agent. CRITICAL RULE: whenever the user asks how many lines, "
    "you MUST call the get_line_count tool and never answer from memory. Keep every "
    "other answer to one short sentence."
)


def _endpoint_and_model(target: str) -> tuple[str, str]:
    if target == "proxy":
        return f"{proxy_base_url()}/v1/chat/completions", "fast-think"
    return f"{tower_base_url()}/v1/chat/completions", "qwen3-coder:30b"


def _to_result(openai_resp: dict) -> UpstreamResult:
    msg = openai_resp["choices"][0]["message"]
    return UpstreamResult(
        model=openai_resp.get("model", ""),
        content=msg.get("content") or "",
        tool_calls=msg.get("tool_calls") or [],
    )


def _score_turn(url: str, model: str, messages: list, expect_tool: bool) -> tuple[bool, str]:
    body = {"model": model, "messages": messages, "max_tokens": 512, "stream": False}
    if expect_tool:
        body["tools"] = _TOOLS
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return False, "upstream_5xx" if exc.code >= 500 else f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError):
        return False, "timeout"

    result = _to_result(payload)
    ok, reason = validate_response(result)
    if not ok:
        return False, reason
    if expect_tool and not result.tool_calls:
        # The model ignored the tool rule - the leg-01 "weak context management"
        # failure mode. Count it as a miss even though the text validates.
        return False, "missed_toolcall"
    return True, "ok"


def run(target: str, turns: int) -> None:
    url, model = _endpoint_and_model(target)
    messages = [{"role": "system", "content": _SYSTEM}]
    reasons: Counter[str] = Counter()
    usable = 0

    for turn in range(turns):
        # Grow context: accumulate a file-shaped blob as a prior tool result.
        messages.append({"role": "user", "content": f"Here is file part {turn}:\n{_BLOB}"})
        messages.append({"role": "assistant", "content": f"Noted file part {turn}."})

        expect_tool = turn % 2 == 1
        if expect_tool:
            messages.append({"role": "user", "content": "How many lines are in the accumulated file context?"})
        else:
            messages.append({"role": "user", "content": "Summarize what you have seen so far in one sentence."})

        ok, reason = _score_turn(url, model, messages, expect_tool)
        reasons[reason] += 1
        usable += int(ok)
        # Feed a plausible assistant turn back so the conversation keeps growing.
        messages.append({"role": "assistant", "content": "acknowledged."})
        print(f"  turn {turn:2d} expect_tool={expect_tool!s:5} -> {'OK' if ok else 'FAIL:' + reason}")

    pct = 100.0 * usable / turns if turns else 0.0
    print(f"\ntarget={target} model={model} runs={turns} reliability={pct:.0f}%")
    print("failure reasons:", dict(reasons))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["direct", "proxy"], default="proxy")
    ap.add_argument("--turns", type=int, default=6)
    args = ap.parse_args()
    run(args.target, args.turns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
