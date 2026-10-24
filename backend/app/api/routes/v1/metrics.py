"""Private-network server reads; API key authentication remains mandatory."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.database import DbSession
from app.schemas.enums import ProviderName, SeriesType
from app.schemas.responses.activity.health_metrics import ChartResolution, HealthChart, MetricAvailability
from app.services import ApiKeyDep
from app.services.health_metrics_service import health_metrics_service

router = APIRouter()


@router.get("/users/{user_id}/metrics/chart")
async def chart(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
    metric: SeriesType,
    start: datetime,
    end: datetime,
    providers: Annotated[list[ProviderName], Query(max_length=5)],
    time_zone: str = "UTC",
    resolution: ChartResolution = ChartResolution.AUTO,
    max_points: Annotated[int, Query(ge=10, le=2000)] = 1000,
) -> HealthChart:
    return health_metrics_service.chart(
        db, user_id, [p.value for p in providers], metric, start, end, time_zone, resolution, max_points
    )


@router.get("/users/{user_id}/metrics/availability")
async def availability(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
    start: datetime,
    end: datetime,
    providers: Annotated[list[ProviderName], Query(max_length=5)],
    metrics: Annotated[list[SeriesType], Query(min_length=1, max_length=12)],
) -> list[MetricAvailability]:
    return health_metrics_service.availability(db, user_id, [p.value for p in providers], metrics, start, end)
