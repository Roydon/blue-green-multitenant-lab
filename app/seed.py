"""Provisions demo tenants.

Only ever inserts the tenants registry row and demo documents against
db-blue (the live side). Against db-green, pass --structure-only: it
provisions identical schema/table/RLS structure (see
public.provision_tenant_schema in db/init/01-schema.sql) but leaves
public.tenants and all row data to arrive one-way via logical replication
(db/replication/setup.sh). Inserting the same registry row independently
on both sides would give logical replication's initial COPY a primary-key
conflict to choke on -- see docs/ARCHITECTURE.md and git history.

Usage:
  python -m app.seed --db-url postgresql://...db-blue... --with-demo-data
  python -m app.seed --db-url postgresql://...db-green... --structure-only
"""
import argparse
import uuid

import psycopg2

TENANTS = [
    {"slug": "acme", "name": "Acme Corp"},
    {"slug": "globex", "name": "Globex Inc"},
]

# Deterministic UUIDs so app/oidc_provider.py's demo users and
# tests/test_isolation.py can reference tenants by a fixed id without a
# lookup round-trip.
TENANT_IDS = {
    "acme": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    "globex": uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
}


def main(db_url: str, structure_only: bool, with_demo_data: bool) -> None:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        for tenant in TENANTS:
            tid = TENANT_IDS[tenant["slug"]]
            if structure_only:
                cur.execute("SELECT public.provision_tenant_schema(%s)", (tenant["slug"],))
                print(f"provisioned schema-only structure for tenant '{tenant['slug']}' ({tid})")
            else:
                cur.execute(
                    "SELECT public.provision_tenant(%s, %s, %s)",
                    (str(tid), tenant["slug"], tenant["name"]),
                )
                print(f"provisioned tenant '{tenant['slug']}' ({tid})")

        if with_demo_data:
            if structure_only:
                raise SystemExit("--with-demo-data and --structure-only are mutually exclusive: "
                                  "the structure-only side gets its data via replication, not by seeding")
            for tenant in TENANTS:
                tid = TENANT_IDS[tenant["slug"]]
                schema = f"tenant_{tenant['slug']}"
                cur.execute(f"SET search_path TO {schema}, public")
                cur.execute(f"SET app.tenant_id = '{tid}'")
                cur.execute(
                    f"INSERT INTO {schema}.documents (id, tenant_id, title, body) "
                    f"VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (str(uuid.uuid4()), str(tid), f"Welcome doc for {tenant['name']}",
                     f"This document belongs only to {tenant['name']}."),
                )
                print(f"seeded demo document for '{tenant['slug']}'")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--structure-only", action="store_true",
                         help="provision schema/tables/RLS only, no tenants row -- use for db-green")
    parser.add_argument("--with-demo-data", action="store_true",
                         help="also seed demo documents -- use for db-blue only")
    args = parser.parse_args()
    main(args.db_url, args.structure_only, args.with_demo_data)
