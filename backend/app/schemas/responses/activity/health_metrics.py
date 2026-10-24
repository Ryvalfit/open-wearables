"""Bounded health reads used by server-side integrations."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ChartResolution(str, Enum):
    AUTO = "AUTO"
    RAW = "RAW"
    MINUTE = "MINUTE"
    FIVE_MINUTES = "FIVE_MINUTES"
    FIFTEEN_MINUTES = "FIFTEEN_MINUTES"
    HOUR = "HOUR"
    DAY = "DAY"
    WEEK = "WEEK"


class HealthSource(BaseModel):
    provider: str
    data_source_id: UUID
    app_name: str | None = None
    device_name: str | None = None


class ChartPoint(BaseModel):
    start_at: datetime
    end_at: datetime | None = None
    value: float
    minimum: float | None = None
    maximum: float | None = None
    sample_count: int | None = None
    representation: str


class ChartSeries(BaseModel):
    source: HealthSource
    points: list[ChartPoint]


class ChartSummary(BaseModel):
    value: float | None = None
    sample_count: int | None = None
    minimum: float | None = None
    maximum: float | None = None


class HealthChart(BaseModel):
    unit: str
    effective_resolution: ChartResolution
    history_precision: str
    summary: ChartSummary
    series: list[ChartSeries] = Field(default_factory=list)


class MetricAvailability(BaseModel):
    type: str
    provider: str
    available: bool
    observation_count: int | None = None
    history_precision: str
