#!/usr/bin/env bash
set -euo pipefail
# Explicit maintenance operation. No database creation or production defaults.
uv run python - <<'PYCHECK'
import os
from sqlalchemy import create_engine, text
from app.config import settings
if os.environ.get("PGSSLMODE") != "verify-full":
    raise RuntimeError("Production bootstrap requires verified PostgreSQL TLS")
with create_engine(settings.db_uri).connect() as connection:
    row = connection.execute(text("SELECT current_database(), current_user, current_setting('server_version_num')::int")).one()
    if row[0] != "open_wearables" or row[1] != "hero_open_wearables" or not 160000 <= row[2] < 170000:
        raise RuntimeError("Unexpected database, role or PostgreSQL version; no migration applied")
    if not connection.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")).scalar_one():
        raise RuntimeError("PostgreSQL connection is not encrypted")
PYCHECK
uv run alembic upgrade head
uv run python scripts/data_migrations/split_google_provider.py
uv run python scripts/init_provider_settings.py
uv run python scripts/init_device_priorities.py
uv run python scripts/init/seed_admin.py
uv run python scripts/init/seed_series_types.py
uv run python scripts/data_migrations/relabel_ultrahuman_body_temp_to_skin_temp.py
uv run python scripts/data_migrations/backfill_whoop_strain_event_record.py
uv run python scripts/init/seed_archival_settings.py
uv run python scripts/init/seed_webhook_event_types.py
uv run alembic current
