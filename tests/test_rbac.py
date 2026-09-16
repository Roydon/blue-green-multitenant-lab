"""Proves the RBAC middleware layer (app/security.py) independently of
tenant isolation: given the RIGHT tenant, does the caller's role gate the
right actions? Tenant isolation (tests/test_isolation.py) covers the
orthogonal failure mode -- right role, wrong tenant.
"""
import httpx

from tests.conftest import APP_URL


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_is_unauthorized():
    resp = httpx.get(f"{APP_URL}/tenants/acme/documents")
    assert resp.status_code == 401


def test_malformed_token_is_unauthorized():
    resp = httpx.get(f"{APP_URL}/tenants/acme/documents", headers=_auth("not-a-real-jwt"))
    assert resp.status_code == 401


def test_member_can_read_and_create(bob_token):
    """bob is a 'member' on acme -- read/create require only 'member'."""
    create = httpx.post(
        f"{APP_URL}/tenants/acme/documents", json={"title": "member doc", "body": "x"}, headers=_auth(bob_token)
    )
    assert create.status_code == 200
    listed = httpx.get(f"{APP_URL}/tenants/acme/documents", headers=_auth(bob_token))
    assert listed.status_code == 200


def test_member_cannot_delete_admin_only_action(bob_token, alice_token):
    """bob (member) must be refused; alice (admin, same tenant) must succeed
    on the exact same resource -- isolates the role check from anything
    tenant-related."""
    created = httpx.post(
        f"{APP_URL}/tenants/acme/documents", json={"title": "to be deleted", "body": ""}, headers=_auth(alice_token)
    ).json()
    doc_id = created["id"]

    member_attempt = httpx.delete(f"{APP_URL}/tenants/acme/documents/{doc_id}", headers=_auth(bob_token))
    assert member_attempt.status_code == 403

    admin_attempt = httpx.delete(f"{APP_URL}/tenants/acme/documents/{doc_id}", headers=_auth(alice_token))
    assert admin_attempt.status_code == 200


def test_admin_on_one_tenant_is_not_admin_on_another(carol_token):
    """carol is an admin, but on 'globex' -- admin rank alone must not grant
    access to 'acme'; tenant scoping is checked independently of role rank."""
    resp = httpx.delete(f"{APP_URL}/tenants/acme/documents/00000000-0000-0000-0000-000000000000",
                         headers=_auth(carol_token))
    assert resp.status_code == 403
