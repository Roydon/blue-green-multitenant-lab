#!/usr/bin/env bash
# The blue -> green cutover: health-gate green, confirm it has caught up
# via logical replication, shift router traffic in steps, and roll back
# to 100% blue automatically on the first failed health check at any step.
#
# Run this from the repo root on the docker host (the VM), with the stack
# already up (`docker compose up -d`) and replication already wired
# (db/replication/setup.sh already run once).
set -uo pipefail
cd "$(dirname "$0")/.."

STEPS=(10 50 100)     # green weight at each step; blue weight is 100-this
PROBE_COUNT=8          # health probes through the router per step
PROBE_SLEEP=0.25
PGPASS="postgres_pw"

log() { echo "[cutover] $*"; }

rollback() {
    log "ROLLBACK: reverting router to 100% blue"
    python3 scripts/render_upstream.py 100 0
    docker compose exec -T router nginx -s reload
    log "rollback complete. Cutover FAILED."
    exit 1
}

probe_router_health() {
    local ok=0 fail=0
    for _ in $(seq 1 "$PROBE_COUNT"); do
        if curl -sf -o /dev/null -m 2 http://localhost:8080/healthz; then
            ok=$((ok+1))
        else
            fail=$((fail+1))
        fi
        sleep "$PROBE_SLEEP"
    done
    log "router health probes: $ok ok, $fail failed"
    [ "$fail" -eq 0 ]
}

log "Step 1/4: health-checking green directly (bypassing the router)"
if ! docker compose exec -T app-green curl -sf -m 5 http://localhost:8000/healthz > /dev/null; then
    log "green failed its own health check before any traffic was shifted -- aborting, no rollback needed"
    exit 1
fi
log "green is healthy"

log "Step 2/4: confirming replication lag is caught up (db-green tracking db-blue)"
BLUE_LSN="$(docker compose exec -T -e PGPASSWORD="$PGPASS" db-blue psql -U postgres -d appdb -tA -c "SELECT pg_current_wal_lsn();")"
LAG_BYTES="$(docker compose exec -T -e PGPASSWORD="$PGPASS" db-green psql -U postgres -d appdb -tA -c \
    "SELECT pg_wal_lsn_diff('${BLUE_LSN}', received_lsn) FROM pg_stat_subscription WHERE subname = 'green_sub';")"
LAG_BYTES="$(echo "$LAG_BYTES" | tr -d '[:space:]')"
if [ -z "$LAG_BYTES" ]; then
    log "could not read replication lag (is db/replication/setup.sh done?) -- aborting, no rollback needed"
    exit 1
fi
log "replication lag: ${LAG_BYTES} bytes behind blue's LSN at gate time"
if [ "$LAG_BYTES" -gt 1048576 ]; then
    log "lag exceeds 1MiB threshold -- green has not caught up, aborting before shifting any traffic"
    exit 1
fi

log "Step 3/4: shifting router traffic in steps: ${STEPS[*]}"
for green_w in "${STEPS[@]}"; do
    blue_w=$((100 - green_w))
    log "-- shifting to blue=${blue_w}% green=${green_w}% --"
    python3 scripts/render_upstream.py "$blue_w" "$green_w"
    docker compose exec -T router nginx -s reload
    if ! probe_router_health; then
        log "health probe failed at blue=${blue_w}%/green=${green_w}%"
        rollback
    fi
done

log "Step 4/4: cutover complete. Router is now 100% green."
log "Cutover SUCCEEDED."
