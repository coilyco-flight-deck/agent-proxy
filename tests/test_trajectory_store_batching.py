"""A batch append connects once, not once per record.

Every operational view rebuild calls `save_all` over the whole retained ledger,
so connecting per record put SQLite connection setup on the critical path of a
read endpoint and scaled it with ledger size. At 2000 retained turns that was
the single largest component of the rebuild. These tests pin the batching so a
refactor cannot quietly restore the per-record connect.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.trajectory import (
    MaterializationStore,
    TrajectoryMaterializer,
    TrajectoryStore,
    materialize_retained_events,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _event(index: int) -> dict:
    """One valid completed event on its own episode, so each yields a trajectory."""
    payload = json.loads((FIXTURES / "valid.json").read_text(encoding="utf-8"))
    # Vary only the tail: the schema requires a real UUIDv7 and the fixture is one.
    payload["event_id"] = f"018f6d1d-5e54-7c20-bf7e-5bd1ca1e8{index:03x}"
    payload["idempotency_key"] = f"fixture-batching-{index}"
    # Every strong correlation must differ: the materializer unions events that
    # share any one of them, so varying only episode_id yields one trajectory.
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


def _count_connects(monkeypatch, store) -> list[int]:
    calls = [0]
    original = store._connect

    def counted() -> sqlite3.Connection:
        calls[0] += 1
        return original()

    monkeypatch.setattr(store, "_connect", counted)
    return calls


def test_materialization_batch_connects_once(tmp_path, monkeypatch):
    derived = MaterializationStore(tmp_path / "trajectory.sqlite3")
    derived.initialize()
    records = TrajectoryMaterializer().materialize(
        tuple(validate_event(_event(index)) for index in range(20))
    )
    assert len(records) == 20, "fixture must yield one trajectory per event"

    calls = _count_connects(monkeypatch, derived)
    saved = derived.save_all(records)

    assert len(saved) == 20
    assert calls[0] == 1, f"expected one connect for the batch, got {calls[0]}"


def test_empty_batch_touches_no_connection(tmp_path, monkeypatch):
    derived = MaterializationStore(tmp_path / "trajectory.sqlite3")
    derived.initialize()
    calls = _count_connects(monkeypatch, derived)

    assert derived.save_all(()) == ()
    assert calls[0] == 0


def test_sharing_a_connection_preserves_the_dedupe_rule(tmp_path):
    """The append-only contract is the thing the batching must not disturb."""
    raw = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    derived = MaterializationStore(raw.path)
    for index in range(5):
        raw.ingest(_event(index))

    first = materialize_retained_events(raw, derived)
    second = materialize_retained_events(raw, derived)

    assert [record.revision for record in first] == [1] * 5
    # An unchanged rebuild re-returns revision 1 rather than appending.
    assert second == first
    for record in first:
        assert len(derived.revisions(record.trajectory_id)) == 1
