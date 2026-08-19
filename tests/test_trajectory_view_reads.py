"""The operational view builder reads the ledger once (#142).

`_operational_builder` runs on every view and dossier request and used to call
`iter_events` twice: once inside `materialize_retained_events` and again for the
evaluation half. That doubled the dominant cost of a read endpoint, and it let
the two halves observe different snapshots when an ingest landed between them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.trajectory import (
    MaterializationStore,
    TrajectoryStore,
    materialize_retained_events,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _event(index: int) -> dict:
    payload = json.loads((FIXTURES / "valid.json").read_text(encoding="utf-8"))
    payload["event_id"] = f"018f6d1d-5e54-7c20-bf7e-5bd1ca1e8{index:03x}"
    payload["idempotency_key"] = f"fixture-view-read-{index}"
    payload["correlation"] = {
        **payload["correlation"],
        "episode_id": f"fixture-episode-{index}",
        "ward_run_id": f"fixture-run-{index}",
        "agent_session_id": f"fixture-session-{index}",
        "request_id": f"fixture-request-{index}",
        "trace_id": f"fixture-trace-{index}",
        "span_id": f"fixture-span-{index}",
    }
    return payload


@pytest.fixture
def ledger(tmp_path):
    raw = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    for index in range(4):
        raw.ingest(_event(index))
    return raw


def test_builder_reads_the_ledger_once(ledger, monkeypatch):
    """Drives the real endpoint helper and counts full-table reads."""
    from app.config import get_settings
    from app.trajectory import api

    monkeypatch.setattr(api, "get_trajectory_store", lambda: ledger)
    monkeypatch.setattr(get_settings(), "trajectory_db_path", ledger.path)

    calls = [0]
    original = ledger.iter_events

    def counted(**kwargs):
        calls[0] += 1
        return original(**kwargs)

    monkeypatch.setattr(ledger, "iter_events", counted)
    api._operational_builder()

    assert calls[0] == 1, f"expected one ledger read per request, got {calls[0]}"


def test_supplied_events_are_the_ones_materialized(ledger, tmp_path):
    """A caller-supplied snapshot wins, so both halves cannot disagree."""
    derived = MaterializationStore(ledger.path)
    everything = tuple(ledger.iter_events())
    subset = everything[:2]

    from_subset = materialize_retained_events(ledger, derived, events=subset)

    assert len(everything) == 4
    assert len(from_subset) == 2, "the supplied snapshot must be what gets materialized"


def test_omitting_events_still_reads_the_store(ledger):
    """The default path is unchanged for every other caller."""
    derived = MaterializationStore(ledger.path)

    assert len(materialize_retained_events(ledger, derived)) == 4
