"""Bounded chart reads over OW originals; no secondary health store."""

from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app.database import DbSession
from app.repositories.health_metrics_repository import HealthMetricsRepository
from app.schemas.enums import SeriesType, get_series_type_id, get_series_type_unit
from app.schemas.enums.aggregation_method import get_aggregation_method
from app.schemas.responses.activity.health_metrics import (
    ChartPoint,
    ChartResolution,
    ChartSeries,
    ChartSummary,
    HealthChart,
    HealthSource,
    MetricAvailability,
)

STEPS = [
    (ChartResolution.MINUTE, 60),
    (ChartResolution.FIVE_MINUTES, 300),
    (ChartResolution.FIFTEEN_MINUTES, 900),
    (ChartResolution.HOUR, 3600),
    (ChartResolution.DAY, 86400),
    (ChartResolution.WEEK, 604800),
]


class HealthMetricsService:
    def __init__(self) -> None:
        self.repository = HealthMetricsRepository()

    @staticmethod
    def validate(start: datetime, end: datetime, tz: str, maximum_days: int) -> None:
        if start.tzinfo is None or end.tzinfo is None or end <= start or end - start > timedelta(days=maximum_days):
            raise HTTPException(400, "Invalid or excessive health window")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(400, "Invalid IANA time zone") from exc

    def chart(
        self,
        db: DbSession,
        user_id: UUID,
        providers: list[str],
        metric: SeriesType,
        start: datetime,
        end: datetime,
        tz: str,
        resolution: ChartResolution,
        max_points: int,
    ) -> HealthChart:
        self.validate(start, end, tz, 5 * 366)
        method = get_aggregation_method(metric).value.upper()
        params = self.repository.parameters(
            user_id, providers, get_series_type_id(metric), start, end, tz, method == "SUM"
        )
        stats = self.repository.summary(db, params)
        count = int(stats["count"])
        precision = self.repository.precision(db, params, count > 0)
        chosen = resolution
        if resolution == ChartResolution.AUTO:
            chosen = ChartResolution.RAW
            if count > max_points:
                chosen = next(
                    (r for r, n in STEPS if (end - start).total_seconds() / n + 2 <= max_points), ChartResolution.WEEK
                )
        if chosen == ChartResolution.RAW:
            bucket = "recorded_at"
        elif chosen in (ChartResolution.DAY, ChartResolution.WEEK):
            unit = "day" if chosen == ChartResolution.DAY else "week"
            bucket = f"(date_trunc('{unit}', recorded_at AT TIME ZONE :tz) AT TIME ZONE :tz)"
        else:
            seconds = dict(STEPS)[chosen]
            bucket = f"to_timestamp(floor(extract(epoch FROM recorded_at)/{seconds})*{seconds})"
        # RAW must distinguish same-time observations; selected contains one source
        # for rates, and one source per local day for counters.
        rows = self.repository.points(db, params, bucket, max_points, method)
        if len(rows) > max_points or (chosen == ChartResolution.RAW and count > max_points):
            raise HTTPException(422, detail={"code": "HEALTH_RESOLUTION_TOO_FINE"})
        groups: dict[UUID, list[ChartPoint]] = defaultdict(list)
        sources: dict[UUID, HealthSource] = {}
        for row in rows:
            source_id = row["data_source_id"]
            sources[source_id] = HealthSource(
                provider=row["provider"],
                data_source_id=source_id,
                app_name=row["app_name"],
                device_name=row["device_name"],
            )
            groups[source_id].append(
                ChartPoint(
                    start_at=row["start_at"],
                    value=float(row["value"]),
                    minimum=float(row["minimum"]),
                    maximum=float(row["maximum"]),
                    sample_count=row["sample_count"],
                    representation="PROVIDER_TOTAL"
                    if row["daily_total"]
                    else ("ORIGINAL" if chosen == ChartResolution.RAW else "BUCKET"),
                )
            )
        field = {"SUM": "total", "MAX": "maximum"}.get(method, "average")
        return HealthChart(
            unit=get_series_type_unit(metric),
            effective_resolution=chosen,
            history_precision=precision,
            summary=ChartSummary(
                value=stats[field], sample_count=count, minimum=stats["minimum"], maximum=stats["maximum"]
            ),
            series=[ChartSeries(source=sources[k], points=v) for k, v in groups.items()],
        )

    def availability(
        self,
        db: DbSession,
        user_id: UUID,
        providers: list[str],
        metrics: list[SeriesType],
        start: datetime,
        end: datetime,
    ) -> list[MetricAvailability]:
        self.validate(start, end, "UTC", 2 * 366)
        mapping = {get_series_type_id(m): m.value for m in metrics}
        rows = self.repository.availability(
            db, dict(user_id=user_id, providers=providers, start=start, end=end), list(mapping)
        )
        return [
            MetricAvailability(
                type=mapping[r["type_id"]],
                provider=r["provider"],
                available=True,
                history_precision="MIXED"
                if r["original"] and r["archived"]
                else ("ORIGINAL" if r["original"] else "AGGREGATED_ONLY"),
            )
            for r in rows
        ]


health_metrics_service = HealthMetricsService()
