"""Mock Auth0/Clerk-compatible OIDC provider.

Runs as its own service so blue and green both trust one issuer, the same
way a real deployment trusts one external IdP regardless of which app
color it's talking to. Issues tenant-scoped, role-carrying JWTs signed with
RS256 and publishes the matching JWKS, exactly like a real OIDC provider --
the RBAC middleware in app/security.py verifies signatures against this
JWKS rather than trusting an unsigned claim.
"""
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from jose import jwt as jose_jwt
from jose.utils import base64url_encode
from pydantic import BaseModel

app = FastAPI(title="mock-oidc-provider")

_KEY_ID = "mock-key-1"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_ISSUER = "http://oidc:9000"
_AUDIENCE = "blue-green-lab-api"

# Demo user directory: who exists, which tenant they belong to, and their role.
# A real integration would replace this with Auth0/Clerk's user database and
# custom-claim rules; the JWT shape produced below is what RBAC middleware
# consumes either way.
_USERS = {
    "alice@tenant-a.test": {"tenant_slug": "acme", "role": "admin"},
    "bob@tenant-a.test": {"tenant_slug": "acme", "role": "member"},
    "carol@tenant-b.test": {"tenant_slug": "globex", "role": "admin"},
}


class TokenRequest(BaseModel):
    username: str
    # Demo only: any non-empty password is accepted, since this provider's
    # job is to prove JWT shape and RBAC enforcement, not credential storage.
    password: str


def _public_jwk() -> dict:
    pub = _PRIVATE_KEY.public_key()
    numbers = pub.public_numbers()
    n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": _KEY_ID,
        "n": base64url_encode(n).decode(),
        "e": base64url_encode(e).decode(),
    }


@app.get("/.well-known/jwks.json")
def jwks():
    return {"keys": [_public_jwk()]}


@app.get("/.well-known/openid-configuration")
def openid_config():
    return {
        "issuer": _ISSUER,
        "jwks_uri": f"{_ISSUER}/.well-known/jwks.json",
        "token_endpoint": f"{_ISSUER}/token",
    }


@app.post("/token")
def issue_token(req: TokenRequest):
    user = _USERS.get(req.username)
    if user is None or not req.password:
        raise HTTPException(status_code=401, detail="invalid credentials")

    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": req.username,
        "tenant": user["tenant_slug"],
        "role": user["role"],
        "iat": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
    }
    pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    access_token = jose_jwt.encode(claims, pem, algorithm="RS256", headers={"kid": _KEY_ID})
    return {"access_token": access_token, "token_type": "bearer", "expires_in": 3600}
