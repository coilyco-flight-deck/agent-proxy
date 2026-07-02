"""Token counting, context budgeting, and self-verification helpers."""

from app.analysis import apply_context_budget, count_tokens, verify_action_claim


def test_count_tokens_nonzero():
    assert count_tokens("hello world") > 0
    assert count_tokens("") == 0


def test_under_budget_is_untouched():
    msgs = [{"role": "user", "content": "short question"}]
    out, total, trimmed = apply_context_budget("fast", msgs, num_ctx=4096, headroom=128)
    assert out == msgs and not trimmed and total > 0


def test_trims_oldest_nonsystem_keeps_system_and_live_turn():
    filler = "word " * 500  # ~500 tokens each
    msgs = [
        {"role": "system", "content": "SYSTEM FRAMING"},
        {"role": "user", "content": "old turn 1 " + filler},
        {"role": "assistant", "content": "old answer 1 " + filler},
        {"role": "user", "content": "the live question"},
    ]
    out, total, trimmed = apply_context_budget("fast", msgs, num_ctx=600, headroom=50)
    assert trimmed
    # system is always kept, the live (last) turn is always kept.
    assert out[0]["role"] == "system"
    assert out[-1]["content"] == "the live question"
    # at least one old turn was dropped.
    assert len(out) < len(msgs)


def test_single_oversized_turn_not_counted_as_avoided():
    # One turn bigger than budget cannot be trimmed - trimmed must be False.
    msgs = [{"role": "user", "content": "word " * 5000}]
    out, total, trimmed = apply_context_budget("fast", msgs, num_ctx=500, headroom=50)
    assert out == msgs and not trimmed


def test_ungrounded_action_claim_is_detected():
    ok, reason = verify_action_claim("I have filed the issue and I am done.", tool_calls=None)
    assert not ok and reason == "ungrounded_action_claim"


def test_tool_evidence_grounds_the_claim():
    ok, reason = verify_action_claim(
        "I have filed the issue and I am done.",
        tool_calls=[{"function": {"name": "create_issue", "arguments": {"title": "x"}}}],
    )
    assert ok and reason == "ok"
