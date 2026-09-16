"""Shared fixtures for the isolation and RBAC suites.

These tests are designed to run *inside* the app-blue container (see
Makefile's `test` target and docker-compose.yml's tests bind mount), so
they can reach both the app under test on localhost:8000 and the OIDC
provider on its docker-network hostname `oidc`, without publishing extra
ports to the host just for testing.
"""
import os

import httpx
import psycopg2
import pytest

APP_URL = os.environ.get("TEST_APP_URL", "http://localhost:8000")
OIDC_URL = os.environ.get("TEST_OIDC_URL", "http://oidc:9000")
DB_DSN = os.environ.get("TEST_DB_DSN", "postgresql://app_user:app_pw@db-blue:5432/appdb")


def _token(username: str) -> str:
    resp = httpx.post(f"{OIDC_URL}/token", json={"username": username, "password": "anything"}, timeout=5.0)
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def alice_token():
    """Admin on tenant 'acme'."""
    return _token("alice@tenant-a.test")


@pytest.fixture(scope="session")
def bob_token():
    """Member (non-admin) on tenant 'acme'."""
    return _token("bob@tenant-a.test")


@pytest.fixture(scope="session")
def carol_token():
    """Admin on tenant 'globex' -- a different tenant from alice/bob."""
    return _token("carol@tenant-b.test")


@pytest.fixture
def db_conn():
    conn = psycopg2.connect(DB_DSN)
    yield conn
    conn.close()
