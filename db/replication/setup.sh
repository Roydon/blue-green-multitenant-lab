#!/usr/bin/env bash
# One-time wiring, run after `docker compose up -d` and after app/seed.py has
# provisioned tenant schemas on BOTH sides: makes db-green a logical
# replica of db-blue, so writes made against blue (the live side) show up
# on green automatically, and the cutover script can gate on replication
# lag being caught up before shifting traffic.
#
# Logical replication only ships row data, not DDL, which is why seed.py
# provisions identical schema/table/RLS structure on both sides first --
# this script wires the data flow on top of that already-matching structure.
set -euo pipefail
cd "$(dirname "$0")/../.."

PGPASS="postgres_pw"
run_blue() { docker compose exec -T -e PGPASSWORD="$PGPASS" db-blue psql -U postgres -d appdb -c "$1"; }
run_green() { docker compose exec -T -e PGPASSWORD="$PGPASS" db-green psql -U postgres -d appdb -c "$1"; }

echo "== creating publication on db-blue =="
run_blue "DROP PUBLICATION IF EXISTS blue_pub;"
run_blue "CREATE PUBLICATION blue_pub FOR ALL TABLES;"

echo "== creating subscription on db-green =="
run_green "DROP SUBSCRIPTION IF EXISTS green_sub;"
run_green "CREATE SUBSCRIPTION green_sub CONNECTION 'host=db-blue port=5432 dbname=appdb user=postgres password=${PGPASS}' PUBLICATION blue_pub;"

echo "== waiting for initial sync =="
for _ in $(seq 1 30); do
    STATE="$(docker compose exec -T -e PGPASSWORD="$PGPASS" db-green psql -U postgres -d appdb -tA \
        -c "SELECT count(*) FROM pg_subscription_rel WHERE srsubstate != 'r';" 2>/dev/null || echo "?")"
    if [ "$STATE" = "0" ]; then
        echo "initial sync complete"
        exit 0
    fi
    sleep 1
done

echo "warning: initial sync did not confirm complete within timeout; check pg_subscription_rel on db-green" >&2
exit 1
