"""Token counting, context budgeting, and self-verification helpers."""

from app import obs
from app.analysis import (
    apply_context_budget,
    count_message_tokens,
    count_tokens,
    detect_context_truncation,
    fit_to_budget,
    verify_action_claim,
)

# --- delivered-context truncation discriminator (issue #33) ----------------- #
#
# The live evidence: qwen3:4b on a NUM_PARALLEL=2 backend, ~100k-token prompt,
# injected num_ctx halved to a per-request window. The proxy asks for `target`
# and the backend delivers `target / NUM_PARALLEL`.


def test_detect_flags_the_halved_window():
    # num_ctx=49152 injected, NUM_PARALLEL=2 -> prompt capped at 24578, prompt was
    # far larger. This is the exact silent halving the proxy must surface.
    assert detect_context_truncation(
        prompt_tokens_sent=48000, prompt_eval_count=24578, target_ctx=49152
    )


def test_detect_flags_the_quartered_window():
    # NUM_PARALLEL=4 (or worse): the shortfall only widens.
    assert detect_context_truncation(
        prompt_tokens_sent=48000, prompt_eval_count=12290, target_ctx=49152
    )


def test_detect_passes_a_full_window_delivery():
    # NUM_PARALLEL=1: a 55k request rides the full injected window (prompt_eval_count
    # ~= num_ctx). Not truncation - the window was delivered as asked.
    assert not detect_context_truncation(
        prompt_tokens_sent=48128, prompt_eval_count=49151, target_ctx=49152
    )


def test_detect_passes_a_small_prompt_that_fit():
    # A short prompt processed in full sits well below num_ctx but was never
    # clipped - the prompt simply had nothing more to show.
    assert not detect_context_truncation(
        prompt_tokens_sent=5000, prompt_eval_count=5000, target_ctx=49152
    )


def test_detect_tolerates_tokenizer_drift():
    # tiktoken estimate (5000) vs ollama's real count (4700) differ by tokenizer,
    # not truncation - the default 15% slack must not flag it.
    assert not detect_context_truncation(
        prompt_tokens_sent=5000, prompt_eval_count=4700, target_ctx=49152
    )


def test_detect_no_signal_when_eval_count_zero():
    # openai-dialect backends / paths that report no prompt_eval_count give no
    # signal to judge - never a false positive.
    assert not detect_context_truncation(
        prompt_tokens_sent=48000, prompt_eval_count=0, target_ctx=49152
    )


def test_count_tokens_nonzero():
    assert count_tokens("hello world") > 0
    assert count_tokens("") == 0


def test_count_tokens_accepts_message_list():
    msgs = [{"role": "user", "content": "hello world"}]
    assert count_tokens(msgs) == count_message_tokens(msgs) > 0


def test_fit_to_budget_trims_over_budget_prompt(monkeypatch):
    filler = "word " * 500  # ~500 tokens each
    msgs = [
        {"role": "system", "content": "SYSTEM FRAMING"},
        {"role": "user", "content": "old turn 1 " + filler},
        {"role": "assistant", "content": "old answer 1 " + filler},
        {"role": "user", "content": "the live question"},
    ]
    num_ctx = 600
    original_tokens = count_tokens(msgs)
    events = []
    attributes = {}

    class Span:
        def is_recording(self):
            return True

        def add_event(self, name, attrs):
            events.append((name, attrs))

        def set_attribute(self, key, value):
            attributes[key] = value

    metric = obs.llm_truncation_avoided_total.labels(logical_model="fit-test")
    before = metric._value.get()
    monkeypatch.setattr("app.obs._current_span", lambda: Span())
    out = fit_to_budget(msgs, num_ctx, logical_model="fit-test")

    # The trimmed prompt fits the safe fraction of the budget.
    assert count_tokens(out) <= int(num_ctx * 0.9)
    # The system framing survives and the live turn is preserved.
    assert any(m["role"] == "system" and m["content"] == "SYSTEM FRAMING" for m in out)
    assert out[-1]["content"] == "the live question"
    # The counter incremented exactly once.
    assert metric._value.get() == before + 1
    # The wrapper emitted a structured event and annotated the current span.
    expected_dropped = len(msgs) - len(out)
    assert events == [
        (
            "request.prompt_trimmed",
            {
                "logical_model": "fit-test",
                "original_token_count": original_tokens,
                "final_token_count": count_tokens(out),
                "budget_tokens": 600 - int(600 * 0.1),
                "target_num_ctx": 600,
                "headroom_tokens": 60,
                "dropped_message_count": expected_dropped,
            },
        )
    ]
    assert attributes["logical_model"] == "fit-test"
    assert attributes["dropped_message_count"] == expected_dropped


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
