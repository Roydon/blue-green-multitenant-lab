#!/usr/bin/env bash
# Blocks until every service with a healthcheck reports healthy, or exits
# non-zero after a timeout. Used by `make test` and scripts/run_demo.sh so
# they don't race container startup.
set -uo pipefail
cd "$(dirname "$0")/.."

for _ in $(seq 1 60); do
    # Services with no healthcheck (router, worker-*) print an empty
    # string here; only services that DO report a health status must
    # say "healthy" -- blank lines are excluded before the check.
    STATUSES="$(docker compose ps --format '{{.Health}}' 2>/dev/null | grep -v '^$' || true)"
    if [ -z "$STATUSES" ]; then
        sleep 2
        continue
    fi
    if ! echo "$STATUSES" | grep -qv '^healthy$'; then
        echo "[wait_healthy] all services healthy"
        exit 0
    fi
    sleep 2
done

echo "[wait_healthy] timed out waiting for services to become healthy" >&2
docker compose ps
exit 1
