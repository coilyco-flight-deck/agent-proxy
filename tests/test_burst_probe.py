"""The burst probe's guards and verdict rules (issue #107)."""

import pytest

from scripts import burst_probe


def _level(**overrides):
    base = {
        "concurrency": 1,
        "issued": 1,
        "admitted": 1,
        "shed": 0,
        "shed_statuses": [],
        "retry_after_present": False,
        "errors": 0,
        "admitted_min_s": 0.1,
        "admitted_median_s": 0.1,
        "admitted_max_s": 0.1,
    }
    base.update(overrides)
    return base


def test_it_refuses_to_spend_tokens_without_consent(capsys):
    with pytest.raises(SystemExit):
        burst_probe.main(["--base-url", "http://ser8:8080", "--model", "sirens-echo/default"])
    assert "real model calls" in capsys.readouterr().err


def test_the_refusal_names_the_cost(capsys):
    with pytest.raises(SystemExit):
        burst_probe.main(
            ["--base-url", "http://x", "--model", "m", "--levels", "1", "5", "--repeats", "2"]
        )
    # 1+5 per round, two rounds.
    assert "12 real model calls" in capsys.readouterr().err


def test_shedding_verdict_warns_about_the_rate_limiter():
    verdict = burst_probe._verdict([_level(), _level(concurrency=30, shed=29, issued=30)])
    assert verdict.startswith("SHED")
    # Issue #110 ships shedding on by default, and it is the likeliest way to
    # read a confidently wrong answer out of this run.
    assert "rate limiter" in verdict


def test_rising_latency_without_rejections_reads_as_queueing():
    verdict = burst_probe._verdict(
        [_level(admitted_median_s=0.1), _level(concurrency=30, admitted_median_s=3.0)]
    )
    assert verdict.startswith("QUEUED")


def test_a_flat_uncontended_run_claims_nothing():
    verdict = burst_probe._verdict(
        [_level(admitted_median_s=0.1), _level(concurrency=30, admitted_median_s=0.12)]
    )
    assert verdict.startswith("NEITHER")
    # The run cannot separate "no admission control" from "the backend coped".
    assert "absorbed the burst" in verdict


def test_a_429_counts_as_shed_not_admitted():
    attempt = burst_probe.Attempt(level=30, round_index=0, status=429, seconds=0.01)
    assert attempt.shed and not attempt.admitted


def test_a_refused_connection_is_recorded_rather_than_lost():
    attempt = burst_probe.Attempt(
        level=30, round_index=0, status=None, seconds=0.01, error="ConnectError"
    )
    assert not attempt.admitted and not attempt.shed
    result = burst_probe.LevelResult(level=30, attempts=[attempt])
    assert result.summary()["errors"] == 1
