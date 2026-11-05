#!/bin/sh
# Zero-downtime-ish deployment: pull, build, migrate, restart, verify.
# Intended to run on the target server from the repository root of `platform/deploy`.
set -eu

COMPOSE=${COMPOSE:-"docker compose"}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8000/api/v1/readyz}

echo "[deploy] 1/6 fetching source"
git pull --ff-only

echo "[deploy] 2/6 validating environment"
[ -f .env ] || { echo "missing .env (copy .env.example and fill values)"; exit 1; }
grep -q "CHANGE_ME" .env && { echo ".env still contains CHANGE_ME placeholders"; exit 1; } || true

echo "[deploy] 3/6 building images"
$COMPOSE build

echo "[deploy] 4/6 starting dependencies"
$COMPOSE up -d postgres redis
for _ in $(seq 1 30); do
  $COMPOSE exec -T postgres pg_isready -U "${POSTGRES_USER:-messenger}" >/dev/null 2>&1 && break
  sleep 2
done

echo "[deploy] 5/6 applying migrations"
$COMPOSE run --rm --entrypoint "" api sh -c "alembic upgrade head && python -m app.cli seed"

echo "[deploy] 6/6 restarting application containers"
$COMPOSE up -d --remove-orphans api nginx backup coturn

echo "[deploy] verifying health at $HEALTH_URL"
for _ in $(seq 1 20); do
  if $COMPOSE exec -T api curl -fsS http://127.0.0.1:8000/api/v1/readyz >/dev/null 2>&1; then
    echo "[deploy] success"
    exit 0
  fi
  sleep 3
done

echo "[deploy] health check failed — inspect: $COMPOSE logs --tail=200 api"
exit 1
