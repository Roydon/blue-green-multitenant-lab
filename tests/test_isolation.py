"""Proves tenant isolation holds at two independent layers:

  1. Schema-per-tenant: each tenant's data lives in its own Postgres schema.
  2. Row-level security: even a query that reaches the WRONG schema (an
     application bug) is filtered down to nothing, because RLS is bound to
     the caller's verified identity (app.tenant_id), not to which table the
     buggy code happened to touch.

test_simulated_app_bug_is_blocked_by_rls and
test_rls_enforced_even_for_raw_sql_client are the two that matter most:
both exercise the exact "tenant A reads tenant B's data through an
application bug" scenario the brief asks for, at the API layer and at the
raw-SQL layer respectively.
"""
import httpx

from app.seed import TENANT_IDS
from tests.conftest import APP_URL


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_each_tenant_sees_only_its_own_seeded_document(alice_token, carol_token):
    acme_docs = httpx.get(f"{APP_URL}/tenants/acme/documents", headers=_auth(alice_token)).json()["documents"]
    globex_docs = httpx.get(f"{APP_URL}/tenants/globex/documents", headers=_auth(carol_token)).json()["documents"]

    assert all(d["tenant_id"] == str(TENANT_IDS["acme"]) for d in acme_docs)
    assert all(d["tenant_id"] == str(TENANT_IDS["globex"]) for d in globex_docs)
    assert not set(d["id"] for d in acme_docs) & set(d["id"] for d in globex_docs)


def test_token_scoped_to_one_tenant_is_rejected_on_another(carol_token):
    """carol authenticates for 'globex' -- her token must not work against
    'acme' at all, regardless of role, before any query is even issued."""
    resp = httpx.get(f"{APP_URL}/tenants/acme/documents", headers=_auth(carol_token))
    assert resp.status_code == 403


def test_simulated_app_bug_is_blocked_by_rls(alice_token):
    """alice is a genuine, correctly-authenticated acme admin. The app has
    a bug: it resolves the wrong schema (globex's) for this request. RLS
    must still return zero rows, because app.tenant_id is bound to alice's
    real tenant (acme), not to the schema the buggy code queried."""
    resp = httpx.get(
        f"{APP_URL}/tenants/acme/documents",
        params={"_simulate_wrong_schema_bug": "tenant_globex"},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bug_simulated"] is True
    assert body["queried_schema"] == "tenant_globex"
    assert body["documents"] == [], (
        "the buggy query reached tenant_globex's schema and still returned rows -- "
        "row-level security failed to isolate tenant acme's session from tenant globex's data"
    )


def test_rls_enforced_even_for_raw_sql_client(db_conn):
    """Bypasses the application entirely: connects as app_user (the same
    low-privilege role the app uses, not a superuser) directly to Postgres,
    points search_path at globex's schema, but sets the RLS session
    variable to acme's tenant id -- simulating any client, buggy or not,
    that ends up in this state. Proves isolation is a database-level
    guarantee, not something only the app's own code paths uphold."""
    with db_conn.cursor() as cur:
        cur.execute("SET search_path TO tenant_globex, public")
        cur.execute("SET app.tenant_id = %s", (str(TENANT_IDS["acme"]),))
        cur.execute("SELECT * FROM documents")
        rows = cur.fetchall()
    assert rows == [], "raw SQL under app_user leaked tenant_globex rows to an acme-scoped session"
