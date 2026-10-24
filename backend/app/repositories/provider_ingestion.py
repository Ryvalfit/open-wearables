"""A deletion fence shared by every provider writer, held through its transaction."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from app.database import DbSession
from app.models.user_connection import UserConnection
from app.schemas.auth import ConnectionStatus


def lock_ingestion(
    db: DbSession, user_id: UUID, provider: str, expected_generation: int | None = None, allow_blocked: bool = False
) -> UserConnection | None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"health-ingestion:{user_id}:{provider}"},
    )
    scopes = db.info.setdefault("health_ingest_generations", {})
    scope = (str(user_id), provider)
    if not allow_blocked:
        if expected_generation is not None:
            scopes[scope] = expected_generation
        expected_generation = scopes.get(scope)
    row = db.query(UserConnection).filter_by(user_id=user_id, provider=provider).populate_existing().first()
    if not allow_blocked and row and row.ingest_blocked_at is not None:
        raise HTTPException(409, "Provider ingestion is blocked")
    generation = row.ingest_generation if row else 0
    if expected_generation is not None and generation != expected_generation:
        raise HTTPException(409, "Provider ingestion generation expired")
    if not allow_blocked:
        scopes.setdefault(scope, generation)
    return row


def set_ingestion_state(db: DbSession, user_id: UUID, provider: str, blocked: bool) -> None:
    row = lock_ingestion(db, user_id, provider, allow_blocked=True)
    if row is None:
        row = UserConnection(
            id=uuid4(),
            user_id=user_id,
            provider=provider,
            status=ConnectionStatus.REVOKED,
            updated_at=datetime.now(timezone.utc),
            ingest_generation=0,
        )
        db.add(row)
    row.ingest_generation = (row.ingest_generation or 0) + 1
    row.ingest_blocked_at = datetime.now(timezone.utc) if blocked else None
    db.commit()


@contextmanager
def provider_lifecycle_lock(db: DbSession, user_id: UUID, provider: str) -> Iterator[None]:
    """Serialize purge/resume across commits without holding a pooled session lock."""
    with db.get_bind().engine.connect() as connection:
        params = {"key": f"health-lifecycle:{user_id}:{provider}"}
        connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:key,0))"), params)
        connection.commit()
        try:
            yield
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), params)
            connection.commit()
