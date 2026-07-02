"""Response validation (leg 04 step 4)."""

from app.resilience import validate_response
from app.upstream import UpstreamResult


def _r(content="", tool_calls=None, thinking=""):
    return UpstreamResult(model="m", content=content, thinking=thinking, tool_calls=tool_calls or [])


def test_empty_content_is_rejected():
    ok, reason = validate_response(_r(""))
    assert not ok and reason == "empty"


def test_thinking_only_is_accepted():
    # A reasoning model that produced thought but ran out of budget before final
    # content did real work - do not reroll it into a 502.
    ok, reason = validate_response(_r(content="", thinking="Let me work through this..."))
    assert ok and reason == "ok"


def test_short_word_answer_is_accepted():
    # A legitimately short reply ("OK", "42", "no") must NOT be rerolled.
    for text in ("OK", "42", "no", "Yes"):
        ok, reason = validate_response(_r(text))
        assert ok, f"{text!r} wrongly rejected as {reason}"


def test_short_nonword_junk_is_truncation_garbage():
    ok, reason = validate_response(_r(".,"))
    assert not ok and reason == "truncation_garbage"


def test_good_toolcall_accepted():
    calls = [{"function": {"name": "search", "arguments": {"q": "hi"}}}]
    ok, _ = validate_response(_r(content="", tool_calls=calls))
    assert ok


def test_malformed_toolcall_rejected():
    calls = [{"function": {"name": "search", "arguments": "{not json"}}]
    ok, reason = validate_response(_r(content="", tool_calls=calls))
    assert not ok and reason == "malformed_toolcall"


def test_repetition_rejected():
    ok, reason = validate_response(_r("na " * 40))
    assert not ok and reason == "repetition"


def test_repeated_line_rejected():
    # A whole multi-word line looped 50x is a stuck decoder - the per-token
    # check misses it (the line carries several distinct words), so the
    # line-level check must catch it.
    ok, reason = validate_response(_r("The quick brown fox jumps over it.\n" * 50))
    assert not ok and reason == "repetition"


def test_varied_multiline_accepted():
    # A genuine numbered list of distinct lines must never be rerolled.
    body = "\n".join(f"{i}. Do the distinct thing number {i} carefully." for i in range(30))
    ok, reason = validate_response(_r(body))
    assert ok and reason == "ok"


def test_normal_answer_accepted():
    ok, reason = validate_response(_r("The capital of France is Paris."))
    assert ok and reason == "ok"


def test_ungrounded_action_claim_is_rejected():
    ok, reason = validate_response(_r("I have filed the issue and I will report back."))
    assert not ok and reason == "ungrounded_action_claim"
