"""
Language analysis and the context-budget guard (leg 02 "language analysis",
leg 04 step 5).

Counts prompt tokens, trims prompts that would overflow a model's safe
``num_ctx``, and does a lightweight self-verification pass over assistant text.
``num_ctx`` injection stops the *default* 32k cliff. The budget guard is the
backstop for a prompt that overflows even the safe ceiling, and the
self-verification helper catches "I did the thing" claims that have no tool or
action evidence behind them.
"""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from .obs import llm_truncation_avoided_total

# Small per-message overhead approximating the chat template's role/delimiter
# tokens. tiktoken's cl100k_base is a close-enough proxy for ollama's tokenizer
# for budgeting; we reserve headroom rather than aim for an exact match.
_PER_MESSAGE_OVERHEAD = 4

_ACTION_LEAD_INS = (
    "i will",
    "i'll",
    "i am going to",
    "i'm going to",
    "i have",
    "i've",
    "i did",
    "i just",
    "i now",
    "i already",
)
_ACTION_VERBS = (
    "file",
    "filed",
    "create",
    "created",
    "open",
    "opened",
    "send",
    "sent",
    "post",
    "posted",
    "email",
    "emailed",
    "submit",
    "submitted",
    "run",
    "ran",
    "call",
    "called",
    "invoke",
    "invoked",
    "use",
    "used",
    "delete",
    "deleted",
    "update",
    "updated",
    "close",
    "closed",
    "push",
    "pushed",
)
_CLAIM_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _ACTION_LEAD_INS) + r")\b.*?\b(?:" + "|".join(_ACTION_VERBS) + r")\b",
    re.IGNORECASE | re.DOTALL,
)


@lru_cache(maxsize=1)
def _encoder():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_encoder().encode(text))


def _message_text(message: dict[str, Any]) -> str:
    """Flatten a message's content, tolerating string or OpenAI content-part lists."""
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        text = " ".join(parts)
    else:
        text = ""
    # Tool calls carried on the message also cost tokens.
    for call in message.get("tool_calls", []) or []:
        text += " " + str(call)
    return text


def count_message_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += count_tokens(_message_text(m)) + _PER_MESSAGE_OVERHEAD
    return total


def detects_ungrounded_action_claim(text: str) -> bool:
    """Heuristic check for a claimed action that should be backed by tool use.

    This is intentionally lightweight. It looks for first-person commitment or
    completion language tied to a concrete action verb, which is enough to catch
    the common "I filed the issue" hallucination without trying to model full
    discourse semantics.
    """
    return bool(_CLAIM_RE.search(text.strip()))


def verify_action_claim(
    text: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a claimed action.

    If the assistant claims to have done something but provides no tool-call
    evidence, reject it so a router can kick the turn back for correction.
    """
    stripped = text.strip()
    if not stripped:
        return True, "ok"
    if not detects_ungrounded_action_claim(stripped):
        return True, "ok"
    if tool_calls:
        return True, "ok"
    return False, "ungrounded_action_claim"


def apply_context_budget(
    logical_model: str,
    messages: list[dict[str, Any]],
    num_ctx: int,
    headroom: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Trim oldest non-system turns until the prompt fits ``num_ctx - headroom``.

    Returns ``(messages, token_count, trimmed)``. System messages are always
    kept (they hold the task framing whose loss caused the leg-01 garbage). The
    most recent turn is kept even if it alone is over budget - we never drop the
    live question. Increments ``llm_truncation_avoided_total`` when it trims.
    """
    budget = max(num_ctx - headroom, 1)
    total = count_message_tokens(messages)
    if total <= budget:
        return messages, total, False

    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]

    system_tokens = count_message_tokens(system)
    # Keep the newest turns; drop from the oldest end of `rest` until we fit.
    kept_rev: list[dict[str, Any]] = []
    running = system_tokens
    for m in reversed(rest):
        cost = count_tokens(_message_text(m)) + _PER_MESSAGE_OVERHEAD
        if running + cost > budget and kept_rev:
            break  # would overflow and we already kept the live turn - stop.
        running += cost
        kept_rev.append(m)

    trimmed_rest = list(reversed(kept_rev))
    # Reassemble preserving original ordering: systems first (their relative
    # order preserved), then the surviving non-system tail in order.
    result = system + trimmed_rest
    new_total = count_message_tokens(result)

    dropped = len(messages) - len(result)
    if dropped <= 0:
        # A single live turn larger than the budget cannot be trimmed (we never
        # drop the current question). We did not actually avoid truncation here -
        # the model will cap it - so do not claim we did.
        return result, new_total, False

    llm_truncation_avoided_total.labels(logical_model=logical_model).inc()
    return result, new_total, True
