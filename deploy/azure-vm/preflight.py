#!/usr/bin/env python3
"""Validate production files without printing credentials or starting services."""
import json
import re
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def read_env(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"{path.name}: require mode 600")
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError(f"{path.name}:{number}: invalid or duplicate key")
        # Plain literal values only; do not source these files as shell scripts.
        if not value or "REPLACE_" in value or "$" in value or value[:1] in "\"'":
            raise ValueError(f"{path.name}:{number}: missing or nonliteral value")
        values[key] = value
    return values


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(root):
    deployment = read_env(root / ".env")
    backend = read_env(root / "backend.env")
    svix = read_env(root / "svix.env")
    redis = read_env(root / "redis.env")
    for key in ["BACKEND_IMAGE", "FRONTEND_IMAGE", "REDIS_IMAGE", "SVIX_IMAGE", "CADDY_IMAGE"]:
        require(re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", deployment.get(key, "")), f"{key}: require an immutable image digest")
    for key in ["ACME_EMAIL"]:
        require("@" in deployment.get(key, ""), f"{key}: require an email")
    expected = {
        "ENVIRONMENT": "production", "DB_HOST": "psql-rvf-prod-neu.postgres.database.azure.com",
        "DB_PORT": "5432", "DB_NAME": "open_wearables", "DB_USER": "hero_open_wearables",
        "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/etc/ssl/certs/ca-certificates.crt",
        "API_BASE_URL": "https://wearables.herofitness.es", "FRONTEND_URL": "http://localhost:3400",
        "CORS_ALLOW_ALL": "false", "REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_DB": "0",
        "REDIS_SSL": "false", "OUTGOING_WEBHOOKS_ENABLED": "true", "SVIX_SERVER_URL": "http://svix:8071",
        "RAW_PAYLOAD_STORAGE": "disabled", "SDK_PAYLOAD_S3_OFFLOAD": "false", "STORE_FIT_FILES": "false",
        "INGEST_WORKOUT_SAMPLES": "false", "LOG_ERROR_RESPONSE_BODY": "false", "DEFAULT_DATA_GRANULARITY": "raw",
    }
    for key, value in expected.items():
        require(backend.get(key) == value, f"backend.env: incompatible {key}")
    require(json.loads(backend.get("CORS_ORIGINS", "null")) == ["http://localhost:3400"], "CORS_ORIGINS: require the SSH portal origin")
    for key, length in [("DB_PASSWORD", 64), ("REDIS_PASSWORD", 64), ("ADMIN_PASSWORD", 64), ("SECRET_KEY", 128), ("SVIX_JWT_SECRET", 64)]:
        require(re.fullmatch(rf"[a-f0-9]{{{length}}}", backend.get(key, "")), f"backend.env: {key} requires {length} hex characters")
    require("@" in backend.get("ADMIN_EMAIL", ""), "ADMIN_EMAIL: require an email")
    require(backend["REDIS_PASSWORD"] == redis.get("REDIS_PASSWORD"), "Redis passwords differ")
    require(backend["SVIX_JWT_SECRET"] == svix.get("SVIX_JWT_SECRET"), "Svix JWT secrets differ")
    require(len({backend[key] for key in ["DB_PASSWORD", "REDIS_PASSWORD", "ADMIN_PASSWORD", "SVIX_JWT_SECRET"]}) == 4, "Generate independent secrets")
    database = urlsplit(svix.get("SVIX_DB_DSN", ""))
    require(database.scheme == "postgresql" and database.hostname == expected["DB_HOST"] and database.port == 5432 and database.path == "/svix" and database.username == "hero_svix", "Svix must use its own database and role on the existing server")
    require(re.fullmatch(r"[a-f0-9]{64}", database.password or ""), "Svix database password requires 64 hex characters")
    require(database.password not in [backend["DB_PASSWORD"], backend["REDIS_PASSWORD"], backend["SVIX_JWT_SECRET"], backend["ADMIN_PASSWORD"]], "Svix database password must be independent")
    require(parse_qs(database.query) == {"sslmode": ["verify-full"], "sslrootcert": [expected["PGSSLROOTCERT"]]}, "Svix database requires verified TLS")
    require(svix.get("SVIX_QUEUE_TYPE") == "redis" and svix.get("SVIX_CACHE_TYPE") == "redis", "Svix requires Redis queue/cache")
    queue = urlsplit(svix.get("SVIX_REDIS_DSN", ""))
    require(queue.scheme == "redis" and queue.hostname == "redis" and queue.port == 6379 and queue.path == "/1" and queue.password == redis["REDIS_PASSWORD"] and not queue.query, "Svix must use Redis logical database 1 with the shared password")
    # Common accidental credential overrides break the expected connection contract.
    for key in ["SVIX_AUTH_TOKEN", "REDIS_USERNAME"]:
        require(key not in backend, f"Remove unsupported override {key}")


if __name__ == "__main__":
    try:
        validate(Path(__file__).resolve().parent)
    except (ValueError, OSError) as error:
        print(f"Preflight failed: {error}", file=sys.stderr)
        sys.exit(1)
    print("Production configuration validated; no service started.")
