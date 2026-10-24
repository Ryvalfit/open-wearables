"""Read-only queries: source filtering precedes source priority and aggregation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from app.database import DbSession

# Both the table names and bucket expressions below are fixed server-side.
FILTERED = """
WITH eligible AS (
 SELECT p.*, s.provider, s.source AS app_name, s.device_model AS device_name,
   COALESCE(pp.priority, 1000000) AS provider_rank,
   COALESCE(dp.priority, 1000000) AS device_rank,
   (p.recorded_at AT TIME ZONE :tz)::date AS local_day
 FROM data_point_series p
 JOIN data_source s ON s.id = p.data_source_id
 LEFT JOIN provider_priority pp ON pp.provider = s.provider
 LEFT JOIN device_type_priority dp ON dp.device_type::text = s.device_type
 WHERE s.user_id = :user_id AND s.provider = ANY(CAST(:providers AS text[]))
   AND p.series_type_definition_id = :type_id
   AND p.recorded_at >= :start AND p.recorded_at < :end
), winners AS (
 SELECT DISTINCT ON (CASE WHEN :counter THEN local_day::timestamp ELSE recorded_at AT TIME ZONE 'UTC' END)
   CASE WHEN :counter THEN local_day::timestamp ELSE recorded_at AT TIME ZONE 'UTC' END AS selection_day,
   data_source_id
 FROM eligible ORDER BY
   CASE WHEN :counter THEN local_day::timestamp ELSE recorded_at AT TIME ZONE 'UTC' END,
   provider_rank, device_rank, data_source_id
), selected AS (
 SELECT e.* FROM eligible e JOIN winners w
   ON w.data_source_id=e.data_source_id
   AND w.selection_day=CASE WHEN :counter THEN e.local_day::timestamp ELSE e.recorded_at AT TIME ZONE 'UTC' END
 WHERE NOT :counter OR (e.is_daily_total IS TRUE AND NOT EXISTS (
   SELECT 1 FROM eligible newer WHERE newer.data_source_id=e.data_source_id
     AND newer.local_day=e.local_day AND newer.is_daily_total IS TRUE
     AND (newer.recorded_at,newer.id) > (e.recorded_at,e.id)
 )) OR (e.is_daily_total IS NOT TRUE AND NOT EXISTS (
   SELECT 1 FROM eligible d WHERE d.data_source_id=e.data_source_id
     AND d.local_day=e.local_day AND d.is_daily_total IS TRUE
 ))
)
"""


class HealthMetricsRepository:
    def parameters(
        self, user_id: UUID, providers: list[str], type_id: int, start: datetime, end: datetime, tz: str, counter: bool
    ) -> dict:
        return dict(user_id=user_id, providers=providers, type_id=type_id, start=start, end=end, tz=tz, counter=counter)

    def summary(self, db: DbSession, params: dict) -> dict:
        return dict(
            db.execute(
                text(
                    FILTERED
                    + """
 SELECT count(*) AS count, sum(value) AS total, avg(value) AS average,
 min(value) AS minimum, max(value) AS maximum FROM selected
"""
                ),
                params,
            )
            .mappings()
            .one()
        )

    def points(self, db: DbSession, params: dict, bucket: str, limit: int, method: str) -> list[dict]:
        # bucket and method only originate from enum maps in the service.
        value = {"AVG": "avg(value)", "SUM": "sum(value)", "MAX": "max(value)"}[method]
        query = (
            FILTERED
            + f"""
 SELECT {bucket} AS start_at, data_source_id, provider, app_name, device_name,
 {value} AS value, min(value) AS minimum, max(value) AS maximum,
 count(*) AS sample_count, bool_or(is_daily_total) AS daily_total
 FROM selected
 GROUP BY start_at, data_source_id, provider, app_name, device_name
 ORDER BY start_at, data_source_id LIMIT :limit
"""
        )
        return [dict(r) for r in db.execute(text(query), {**params, "limit": limit + 1}).mappings()]

    def precision(self, db: DbSession, params: dict, live: bool) -> str:
        archived = db.execute(
            text("""
 SELECT EXISTS (
 SELECT 1 FROM data_point_series_archive a JOIN data_source s ON s.id=a.data_source_id
 WHERE s.user_id=:user_id AND s.provider=ANY(CAST(:providers AS text[]))
 AND a.series_type_definition_id=:type_id
 AND a.bucket_start_at >= :start AND a.bucket_start_at < :end)
"""),
            params,
        ).scalar()
        return ("MIXED" if live else "AGGREGATED_ONLY") if archived else ("ORIGINAL" if live else "UNKNOWN")

    def availability(self, db: DbSession, params: dict, type_ids: list[int]) -> list[dict]:
        return [
            dict(r)
            for r in db.execute(
                text("""
 SELECT d.series_type_definition_id AS type_id, s.provider,
 bool_or(d.original) AS original, bool_or(NOT d.original) AS archived
 FROM (
 SELECT data_source_id, series_type_definition_id, true AS original
 FROM data_point_series WHERE recorded_at >= :start AND recorded_at < :end
 AND series_type_definition_id=ANY(CAST(:types AS int[]))
 UNION ALL
 SELECT data_source_id, series_type_definition_id, false AS original
 FROM data_point_series_archive WHERE bucket_start_at >= :start AND bucket_start_at < :end
 AND series_type_definition_id=ANY(CAST(:types AS int[]))
 ) d JOIN data_source s ON s.id=d.data_source_id
 WHERE s.user_id=:user_id AND s.provider=ANY(CAST(:providers AS text[]))
 GROUP BY d.series_type_definition_id,s.provider
"""),
                {**params, "types": type_ids},
            ).mappings()
        ]
