"""Body-safe backup and replay evidence for a deployed trajectory ledger."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.trajectory.store import ReplayResult, TrajectoryStore


class TrajectoryRecoveryEvidence(BaseModel):
    """Counts-only recovery evidence that never serializes retained envelopes."""

    model_config = ConfigDict(extra="forbid")

    source_path: str
    backup_path: str
    replay_path: str
    source_events: int
    backup_events: int
    replay_events: int
    source_quarantined_receipts: int
    replay: ReplayResult
    passed: bool


def verify_trajectory_recovery(
    source_path: str | Path,
    backup_path: str | Path,
    replay_path: str | Path,
) -> TrajectoryRecoveryEvidence:
    """Back up a ledger and prove canonical replay into a fresh store."""

    source = Path(source_path).resolve()
    backup = Path(backup_path).resolve()
    replay = Path(replay_path).resolve()
    if len({source, backup, replay}) != 3:
        raise ValueError("source, backup, and replay paths must be distinct")
    for output in (backup, replay):
        if output.exists():
            raise FileExistsError(f"refusing to overwrite evidence path: {output}")

    source_store = TrajectoryStore(source)
    source_events = len(tuple(source_store.iter_events()))
    source_outcomes = source_store.receipt_outcomes()
    source_store.backup_to(backup)

    backup_store = TrajectoryStore(backup)
    backup_events = len(tuple(backup_store.iter_events()))
    replay_store = TrajectoryStore(replay)
    replay_result = backup_store.replay_into(replay_store, record_receipts=False)
    replay_events = len(tuple(replay_store.iter_events()))
    quarantined = source_outcomes.count("quarantined")

    passed = (
        source_events == backup_events == replay_events
        and replay_result.attempted == source_events
        and replay_result.accepted == source_events
        and replay_result.duplicates == 0
        and replay_result.quarantined == 0
        and quarantined == 0
    )
    return TrajectoryRecoveryEvidence(
        source_path=str(source),
        backup_path=str(backup),
        replay_path=str(replay),
        source_events=source_events,
        backup_events=backup_events,
        replay_events=replay_events,
        source_quarantined_receipts=quarantined,
        replay=replay_result,
        passed=passed,
    )
