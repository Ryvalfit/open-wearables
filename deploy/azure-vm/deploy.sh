#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Prevent parallel maintenance operations on this VM. Never source environment files.
exec 9>.deployment.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
python3 preflight.py
docker compose --env-file .env -f compose.yml config --quiet
docker compose --env-file .env -f compose.yml --profile maintenance pull
# Scheduled work must not overlap schema changes or a new scheduler.
docker compose --env-file .env -f compose.yml stop beat worker proxy api portal svix
# Recreate to apply selected digests and secrets; Svix owns its migrations.
docker compose --env-file .env -f compose.yml up -d --wait --wait-timeout 180 redis svix
docker compose --env-file .env -f compose.yml run --rm --no-deps bootstrap
docker compose --env-file .env -f compose.yml up -d --wait --wait-timeout 180 api worker beat portal proxy
echo 'Services started. Verify public HTTPS, device sync and signed Hero delivery.'
