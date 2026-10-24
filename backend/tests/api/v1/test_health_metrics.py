"""Database/API contracts for private health reads and provider deletion fences."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import ApiKey, DataPointSeries, DataSource, User
from app.repositories.provider_ingestion import lock_ingestion, set_ingestion_state
from app.schemas.enums import ProviderName, SeriesType, get_series_type_id
from tests.factories import ApiKeyFactory, DataPointSeriesFactory, DataSourceFactory, UserFactory
from tests.utils import api_key_headers

START = datetime(2026, 10, 24, tzinfo=timezone.utc)


def setup_read() -> tuple[User, ApiKey, DataSource]:
    user = UserFactory()
    key = ApiKeyFactory()
    source = DataSourceFactory(user=user, provider=ProviderName.APPLE, device_model="watch")
    return user, key, source


def sample(
    source: DataSource,
    value: float,
    seconds: int = 0,
    metric: SeriesType = SeriesType.heart_rate,
    daily: bool | None = None,
) -> DataPointSeries:
    return DataPointSeriesFactory(
        mapping=source,
        value=value,
        recorded_at=START + timedelta(seconds=seconds),
        series_type=SimpleNamespace(id=get_series_type_id(metric)),
        is_daily_total=daily,
    )


def query(client: TestClient, user: User, key: Any, **overrides: Any) -> Response:
    params = dict(
        metric="heart_rate",
        providers=["apple"],
        start=START.isoformat(),
        end=(START + timedelta(days=1)).isoformat(),
        time_zone="Europe/Madrid",
        resolution="RAW",
        max_points=10,
    )
    params.update(overrides)
    return client.get(f"/api/v1/users/{user.id}/metrics/chart", params=params, headers=api_key_headers(key.plain_key))


def test_source_scope_precedes_priority_and_statistics(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    private = DataSourceFactory(user=user, provider=ProviderName.GARMIN)
    other = DataSourceFactory(user=UserFactory(), provider=ProviderName.APPLE)
    sample(source, 65)
    sample(private, 220)
    sample(other, 230)
    r = query(client, user, key)
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == dict(value=65.0, sample_count=1, minimum=65.0, maximum=65.0)
    assert {s["source"]["provider"] for s in r.json()["series"]} == {"apple"}


def test_raw_identity_and_cursor_keep_same_time_devices(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    second = DataSourceFactory(user=user, provider=ProviderName.APPLE, device_model="phone")
    sample(source, 60)
    sample(second, 90)
    params = dict(
        types="heart_rate",
        provider="apple",
        resolution="raw",
        start_time=START.isoformat(),
        end_time=(START + timedelta(days=1)).isoformat(),
        limit=1,
        include_total_count=False,
    )
    url = f"/api/v1/users/{user.id}/timeseries"
    a = client.get(url, params=params, headers=api_key_headers(key.plain_key)).json()
    b = client.get(
        url, params={**params, "cursor": a["pagination"]["next_cursor"]}, headers=api_key_headers(key.plain_key)
    ).json()
    assert a["data"][0]["id"] != b["data"][0]["id"]
    assert a["data"][0]["data_source_id"] != b["data"][0]["data_source_id"]
    assert {a["data"][0]["value"], b["data"][0]["value"]} == {60, 90}
    assert a["pagination"]["total_count"] is None


def test_buckets_preserve_extremes_and_full_window_weight(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    sample(source, 40)
    sample(source, 100, 1)
    sample(source, 160, 61)
    r = query(client, user, key, resolution="MINUTE")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["summary"]["value"] == 100
    assert data["summary"]["sample_count"] == 3
    assert data["summary"]["minimum"] == 40
    assert data["summary"]["maximum"] == 160
    assert [p["sample_count"] for p in data["series"][0]["points"]] == [2, 1]


def test_daily_counter_uses_reported_total_once(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    sample(source, 100, 0, SeriesType.steps, False)
    sample(source, 1100, 1800, SeriesType.steps, True)
    sample(source, 1200, 3600, SeriesType.steps, True)
    r = query(client, user, key, metric="steps", resolution="DAY")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["value"] == 1200


def test_dst_day_boundaries_and_exclusive_end(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    sample(source, 70)
    sample(source, 200, 24 * 3600)
    r = query(client, user, key, resolution="DAY")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["sample_count"] == 1
    assert r.json()["series"][0]["points"][0]["start_at"] == "2026-10-23T22:00:00Z"


def test_raw_budget_requires_pagination_instead_of_silent_loss(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    for i in range(11):
        sample(source, 60 + i, i)
    r = query(client, user, key)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "HEALTH_RESOLUTION_TOO_FINE"


def test_delete_blocks_writers_until_explicit_new_generation(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    set_ingestion_state(db, user.id, "apple", blocked=True)
    with pytest.raises(HTTPException, match="blocked"):
        lock_ingestion(db, user.id, "apple")
    db.rollback()
    set_ingestion_state(db, user.id, "apple", blocked=False)
    with pytest.raises(HTTPException, match="generation"):
        lock_ingestion(db, user.id, "apple", expected_generation=0)
    db.rollback()
    row = lock_ingestion(db, user.id, "apple", expected_generation=2)
    assert row.ingest_blocked_at is None


def test_sleep_priority_only_sees_allowed_sources_and_includes_overlaps(client: TestClient, db: Session) -> None:
    from tests.factories import EventRecordFactory, SleepDetailsFactory

    user, key, source = setup_read()
    hidden = DataSourceFactory(user=user, provider=ProviderName.GARMIN)
    for origin in [source, hidden]:
        record = EventRecordFactory(
            mapping=origin,
            category="sleep",
            type="sleep",
            start_datetime=START - timedelta(hours=1),
            end_datetime=START + timedelta(hours=7),
            duration_seconds=28800,
        )
        SleepDetailsFactory(event_record=record, sleep_total_duration_minutes=450)
    response = client.get(
        f"/api/v1/users/{user.id}/events/sleep",
        params=dict(
            start_date=START.isoformat(),
            end_date=(START + timedelta(days=1)).isoformat(),
            providers=["apple"],
            filter_by_priority=True,
            overlap_window=True,
        ),
        headers=api_key_headers(key.plain_key),
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["source"]["provider"] == "apple"


def test_sleep_state_cannot_resurrect_a_previous_ingest_generation(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.providers.mobile_sdk import SleepState
    from app.services.sdk.sleep_service import finish_sleep

    user, _, _ = setup_read()
    state = SleepState(
        uuid="old",
        provider="apple",
        start_time=START,
        end_time=START + timedelta(hours=7),
        last_start_timestamp=START,
        last_end_timestamp=START,
    )
    set_ingestion_state(db, user.id, "apple", blocked=False)
    removed = []
    monkeypatch.setattr("app.services.sdk.sleep_service.delete_sleep_state", removed.append)
    with pytest.raises(HTTPException, match="generation"):
        finish_sleep(db, str(user.id), state)
    assert removed == [str(user.id)]


def test_rate_priority_keeps_other_devices_when_the_winner_has_no_sample(client: TestClient, db: Session) -> None:
    user, key, source = setup_read()
    second = DataSourceFactory(user=user, provider=ProviderName.APPLE, device_model="another watch")
    sample(source, 60)
    sample(second, 60)
    sample(source, 80, 1)
    sample(second, 100, 2)
    response = query(client, user, key)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == dict(value=80.0, sample_count=3, minimum=60.0, maximum=100.0)
    assert sum(len(series["points"]) for series in response.json()["series"]) == 3
