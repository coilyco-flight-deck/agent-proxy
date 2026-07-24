"""Cold-path HTTP intake for trajectory contract v1 deliveries."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.trajectory.evaluation import EvaluationStore, assemble_evaluation_records
from app.trajectory.materialize import MaterializationStore, materialize_retained_events
from app.trajectory.store import TrajectoryStore
from app.trajectory.views import OperationalViewBuilder, ViewName

router = APIRouter()


@lru_cache(maxsize=1)
def get_trajectory_store() -> TrajectoryStore:
    return TrajectoryStore(get_settings().trajectory_db_path)


@router.post("/v1/trajectory/events")
async def ingest_trajectory_event(request: Request) -> JSONResponse:
    try:
        payload: Any = await request.json()
    except Exception:
        payload = await request.body()
    result = await get_trajectory_store().ingest_async(payload)
    status_code = {
        "accepted": 202,
        "duplicate": 200,
        "quarantined": 422,
    }[result.outcome]
    return JSONResponse(status_code=status_code, content=result.as_dict())


def _operational_builder() -> OperationalViewBuilder:
    path = get_settings().trajectory_db_path
    raw = get_trajectory_store()
    derived = MaterializationStore(path)
    trajectories = materialize_retained_events(raw, derived)
    events = tuple(raw.iter_events())
    evaluation_store = EvaluationStore(path)
    evaluations = evaluation_store.save_all(assemble_evaluation_records(events, trajectories))
    return OperationalViewBuilder(trajectories, evaluations)


@router.get("/v1/trajectory/views/{view_name}")
async def trajectory_view(view_name: str) -> JSONResponse:
    allowed = {
        "reliability",
        "cost_latency",
        "policy",
        "evaluation",
        "harness_fit",
    }
    if view_name not in allowed:
        return JSONResponse(status_code=404, content={"error": "unknown trajectory view"})
    view = await asyncio.to_thread(lambda: _operational_builder().build(cast(ViewName, view_name)))
    return JSONResponse(content=view.model_dump(mode="json"))


@router.get("/v1/trajectory/dossiers/{trajectory_id}")
async def trajectory_dossier(trajectory_id: str) -> JSONResponse:
    dossier = await asyncio.to_thread(lambda: _operational_builder().dossier(trajectory_id))
    if dossier is None:
        return JSONResponse(status_code=404, content={"error": "trajectory not found"})
    return JSONResponse(content=dossier.model_dump(mode="json"))
