"""Token counting and context budgeting helpers."""

import pytest

from app import obs
from app.analysis import (
    PromptPairingError,
    apply_context_budget,
    count_message_tokens,
    count_tokens,
    detect_context_truncation,
    fit_to_budget,
    group_tool_call_turns,
    unpaired_tool_message,
)

# Delivered-context truncation discriminator (issue #33). Live evidence:
# qwen3:4b on a NUM_PARALLEL=2 backend delivered `target / NUM_PARALLEL`.


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


# Tool-call pairing under trimming (issue #113). A tool-heavy round crosses the
# budget first, and dropping half a group is what the backend rejects with 400.


def _tool_round(call_id: str, filler: str) -> list[dict]:
    """One assistant tool call plus its reply, sized to dominate the budget."""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "steam__get_store_app_details", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": "app details " + filler},
    ]


def test_grouping_keeps_a_tool_reply_with_its_call():
    groups = group_tool_call_turns(
        [
            {"role": "user", "content": "question"},
            *_tool_round("call_a", "x"),
            {"role": "assistant", "content": "answer"},
        ]
    )
    assert [len(group) for group in groups] == [1, 2, 1]
    assert groups[1][0]["role"] == "assistant" and groups[1][1]["role"] == "tool"


def test_grouping_collects_every_reply_to_one_assistant_turn():
    calls = [
        {"id": f"call_{n}", "type": "function", "function": {"name": "f", "arguments": "{}"}}
        for n in range(5)
    ]
    messages = [
        {"role": "assistant", "content": None, "tool_calls": calls},
        *[{"role": "tool", "tool_call_id": f"call_{n}", "content": "r"} for n in range(5)],
    ]
    groups = group_tool_call_turns(messages)
    # The trace in issue #113 carried exactly five replies to one assistant turn.
    assert len(groups) == 1 and len(groups[0]) == 6


def test_grouping_is_a_no_op_without_tool_calls():
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    assert group_tool_call_turns(messages) == [[m] for m in messages]


def test_trim_never_orphans_a_tool_reply():
    filler = "word " * 500  # ~500 tokens each
    msgs = [
        {"role": "system", "content": "SYSTEM FRAMING"},
        {"role": "user", "content": "old question " + filler},
        *_tool_round("call_old", filler),
        {"role": "user", "content": "the live question"},
        *_tool_round("call_live", "small"),
    ]
    # Tight enough that the old round cannot ride along - the round the unfixed
    # trimmer split in half.
    out, _total, trimmed = apply_context_budget("fast", msgs, num_ctx=350, headroom=50)

    assert trimmed
    # The old round went whole: neither half survives on its own.
    assert not any(m.get("tool_call_id") == "call_old" for m in out)
    assert not any(
        call.get("id") == "call_old" for m in out for call in (m.get("tool_calls") or [])
    )
    # The surviving prompt satisfies the contract the backend enforces.
    assert unpaired_tool_message(out) == ""


def test_trim_keeps_the_live_tool_round_whole_when_it_alone_is_over_budget():
    filler = "word " * 500
    msgs = [
        {"role": "user", "content": "old question " + filler},
        *_tool_round("call_live", filler),
    ]
    out, _total, _trimmed = apply_context_budget("fast", msgs, num_ctx=200, headroom=50)
    # Never drop the live question, and never split it - both halves ride.
    assert [m["role"] for m in out] == ["assistant", "tool"]
    assert unpaired_tool_message(out) == ""


def test_unpaired_input_fails_locally_with_the_offending_message():
    filler = "word " * 500
    msgs = [
        {"role": "user", "content": "old question " + filler},
        {"role": "user", "content": "another " + filler},
        {"role": "tool", "tool_call_id": "call_ghost", "content": "orphan reply"},
    ]
    with pytest.raises(PromptPairingError) as caught:
        apply_context_budget("fast", msgs, num_ctx=600, headroom=50)
    assert "call_ghost" in str(caught.value)


def test_pairing_check_accepts_a_well_formed_prompt():
    assert (
        unpaired_tool_message(
            [
                {"role": "user", "content": "q"},
                *_tool_round("call_a", "x"),
                {"role": "assistant", "content": "a"},
            ]
        )
        == ""
    )


def test_pairing_check_rejects_a_reply_with_no_call():
    reason = unpaired_tool_message([{"role": "tool", "content": "orphan"}])
    assert "no preceding tool call" in reason
