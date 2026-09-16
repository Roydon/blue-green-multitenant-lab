"""RBAC middleware: verifies tenant-scoped JWTs against the mock OIDC JWKS
and enforces role requirements per route.

This is the enforcement layer -- it decides who is allowed to call an
endpoint at all. Row-level security (db/init/01-schema.sql) is the second,
independent layer that stops a bug in this layer (or in the endpoint code)
from crossing tenant boundaries. Neither layer alone is the whole story;
tests/test_isolation.py and tests/test_rbac.py each attack one of them.
"""
import time
from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException, Request
from jose import jwk, jwt
from jose.exceptions import JWTError

from app.config import get_settings

ROLE_RANK = {"member": 1, "admin": 2}


@lru_cache
def _jwks_cache_key() -> str:
    # lru_cache needs a hashable key; settings are frozen for process life.
    return get_settings().oidc_internal_url


_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}


def _get_jwks() -> dict:
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["fetched_at"] > 300:
        url = f"{get_settings().oidc_internal_url}/.well-known/jwks.json"
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()
        _jwks_cache["fetched_at"] = now
    return _jwks_cache["keys"]


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="malformed token") from exc

    jwks = _get_jwks()
    key = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if key is None:
        raise HTTPException(status_code=401, detail="unknown signing key")

    try:
        claims = jwt.decode(
            token,
            jwk.construct(key, algorithm="RS256"),
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
    return claims


async def get_current_claims(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return decode_token(auth[7:])


def require_role(minimum: str):
    """FastAPI dependency: 401 with no/bad token, 403 if role is too low."""

    async def _check(claims: dict = Depends(get_current_claims)) -> dict:
        role = claims.get("role")
        if role not in ROLE_RANK or ROLE_RANK[role] < ROLE_RANK[minimum]:
            raise HTTPException(status_code=403, detail=f"role '{minimum}' or higher required")
        return claims

    return _check


def require_tenant_match(tenant_slug: str, claims: dict) -> None:
    """A valid, sufficiently-privileged token for tenant A must not act on tenant B --
    this is what stops a stolen/reused token from crossing tenants even before
    a query is ever issued."""
    if claims.get("tenant") != tenant_slug:
        raise HTTPException(status_code=403, detail="token is not scoped to this tenant")
