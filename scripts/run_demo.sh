#!/usr/bin/env bash
# End-to-end demo: bring up blue, seed tenants on both sides, wire
# replication, start a continuous load generator against the router, run
# the cutover onto green while load keeps flowing, then check the load
# generator recorded zero failed requests. This is what `make demo` runs.
set -uo pipefail
cd "$(dirname "$0")/.."

OUT_DIR="demo-output"
mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/locust_stats*.csv

fail() { echo "[demo] FAILED: $*" >&2; exit 1; }

command -v locust >/dev/null 2>&1 || fail \
    "'locust' not found on PATH. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"

echo "[demo] bringing up the stack (blue live, green healthy but idle)"
docker compose up -d --build || fail "docker compose up failed"

echo "[demo] waiting for all services to report healthy"
bash scripts/wait_healthy.sh || fail "services did not become healthy in time"

echo "[demo] provisioning tenants on db-blue and db-green (schema-per-tenant, RLS)"
docker compose exec -T app-blue python -m app.seed --db-url postgresql://postgres:postgres_pw@db-blue:5432/appdb --with-demo-data \
    || fail "seeding db-blue failed"
docker compose exec -T app-blue python -m app.seed --db-url postgresql://postgres:postgres_pw@db-green:5432/appdb --structure-only \
    || fail "provisioning db-green structure failed"

echo "[demo] wiring logical replication: db-green tracks db-blue"
bash db/replication/setup.sh || fail "replication setup failed"

echo "[demo] resetting router to 100% blue"
python3 scripts/render_upstream.py 100 0
docker compose exec -T router nginx -s reload

echo "[demo] starting continuous load generator against the router"
locust -f loadgen/locustfile.py --headless -u 8 -r 4 \
    --run-time 90s --csv "$OUT_DIR/locust_stats" --only-summary \
    > "$OUT_DIR/locust.log" 2>&1 &
LOCUST_PID=$!
sleep 5   # let a few users ramp up and confirm the router is serving before we cut over

echo "[demo] running the blue -> green cutover while load is live"
if ! bash scripts/cutover.sh; then
    kill "$LOCUST_PID" 2>/dev/null
    wait "$LOCUST_PID" 2>/dev/null
    fail "cutover script reported failure -- see output above"
fi

echo "[demo] cutover finished; letting load generator run out its duration"
wait "$LOCUST_PID"

echo "[demo] checking load generator results for failures"
STATS_FILE="$OUT_DIR/locust_stats_stats.csv"
if [ ! -f "$STATS_FILE" ]; then
    fail "locust stats file not found at $STATS_FILE"
fi
python3 scripts/check_locust_stats.py "$STATS_FILE" || fail "load generator recorded failed request(s) during the cutover"

echo "[demo] demonstrating queue backpressure with a job burst against the now-live green stack"
# QUEUE_MAX_DEPTH defaults to 150 (app/config.py); firing well past that at
# high concurrency, faster than 2 workers can drain at ~0.25s/job each, is
# what actually pushes the queue over the limit so some submissions come
# back 429 instead of just demonstrating that a small burst gets absorbed.
python3 loadgen/burst_load.py --count 900 --concurrency 100 || fail "burst load script failed"

echo "[demo] SUCCESS: zero failed requests across the cutover, backpressure demonstrated."
