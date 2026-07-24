"""Durable append-only trajectory ingestion and replay."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.trajectory import AsyncTrajectoryEmitter, TrajectoryStore

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_accepted_event_survives_store_reopen(tmp_path):
    path = tmp_path / "trajectory.sqlite3"
    result = TrajectoryStore(path).ingest(_fixture("valid.json"))

    reopened = TrajectoryStore(path)
    event = reopened.load_event(result.event_id or "")
    assert result.outcome == "accepted"
    assert event is not None
    assert str(event.event_id) == result.event_id
    assert reopened.receipt_outcomes() == ("accepted",)


def test_duplicate_delivery_retains_receipt_without_second_event(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")

    original = store.ingest(_fixture("duplicate-original.json"))
    duplicate = store.ingest(_fixture("duplicate-redelivery.json"))

    assert original.outcome == "accepted"
    assert duplicate.outcome == "duplicate"
    assert duplicate.canonical_event_id == original.event_id
    assert len(tuple(store.iter_events())) == 1
    assert store.receipt_outcomes() == ("accepted", "duplicate")


def test_rekeyed_duplicate_creates_a_resolvable_alias(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    original_payload = _fixture("duplicate-original.json")
    original = store.ingest(original_payload)
    rekeyed = dict(original_payload)
    rekeyed["event_id"] = "018f6d1d-5e54-7c20-bf7e-5bd1ca1e81a1"

    duplicate = store.ingest(rekeyed)

    assert duplicate.outcome == "duplicate"
    alias = store.load_event(rekeyed["event_id"])
    assert alias is not None
    assert str(alias.event_id) == original.event_id


def test_invalid_delivery_is_quarantined_with_raw_receipt(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")

    result = store.ingest(_fixture("invalid-missing-event-id.json"))

    assert result.outcome == "quarantined"
    assert result.errors
    assert tuple(store.iter_events()) == ()
    assert store.receipt_outcomes() == ("quarantined",)


def test_store_tables_reject_update_and_delete(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    result = store.ingest(_fixture("valid.json"))

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE events SET event_type = 'execution.failed' WHERE event_id = ?",
                (result.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM receipts")


def test_replay_rebuilds_a_fresh_consumer_and_records_receipt(tmp_path):
    source = TrajectoryStore(tmp_path / "source.sqlite3")
    target = TrajectoryStore(tmp_path / "target.sqlite3")
    source.ingest(_fixture("valid.json"))
    source.ingest(_fixture("late-event.json"))

    result = source.replay_into(target)

    assert result.attempted == 2
    assert result.accepted == 2
    assert len(tuple(target.iter_events())) == 2
    assert source.receipt_outcomes() == (
        "accepted",
        "accepted",
        "replayed",
        "replayed",
    )


async def test_async_emitter_is_bounded_and_flushes_to_durable_store(tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    emitter = AsyncTrajectoryEmitter(store, maxsize=1)

    assert emitter.emit_nowait(_fixture("valid.json")) is True
    assert emitter.emit_nowait(_fixture("late-event.json")) is False
    assert emitter.dropped == 1

    await emitter.start()
    await emitter.stop()

    assert len(tuple(store.iter_events())) == 1


async def test_async_emitter_survives_one_persistence_failure():
    class FlakyStore:
        def __init__(self):
            self.calls = 0

        async def ingest_async(self, _payload):
            self.calls += 1
            if self.calls == 1:
                raise OSError("fixture persistence failure")

    store = FlakyStore()
    emitter = AsyncTrajectoryEmitter(store, maxsize=2)
    await emitter.start()

    assert emitter.emit_nowait({"event": 1})
    assert emitter.emit_nowait({"event": 2})
    await emitter.stop()

    assert store.calls == 2
    assert emitter.failed == 1
    assert emitter.last_error_class == "OSError"
