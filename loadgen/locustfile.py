"""Load generator that runs continuously across the blue->green cutover.

`make demo` starts this headless against the router (localhost:8080) before
running scripts/cutover.sh, then checks its stats afterward. Because every
request goes through the router the same way a real client would, a
connection dropped during any traffic-shift step shows up as a Locust
failure -- this is the "zero failed requests across the switch" proof, not
the cutover script's own internal health probes.

A token is fetched once per simulated user (directly against the OIDC
provider's published port, since the router only proxies the app) and
reused for the run, the way a real client would cache a short-lived access
token instead of re-authenticating on every call.
"""
import httpx
from locust import HttpUser, between, task

OIDC_TOKEN_URL = "http://localhost:9000/token"


class TenantUser(HttpUser):
    wait_time = between(0.2, 0.5)
    host = "http://localhost:8080"

    def on_start(self):
        resp = httpx.post(
            OIDC_TOKEN_URL,
            json={"username": "alice@tenant-a.test", "password": "anything"},
            timeout=5.0,
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]

    @task
    def read_documents(self):
        self.client.get(
            "/tenants/acme/documents",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/tenants/acme/documents",
        )
