"""Cold-path HTTP intake for trajectory contract v1 deliveries."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.trajectory.store import TrajectoryStore

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
