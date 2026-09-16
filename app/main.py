"""Main app service. Identical image runs as both blue and green; APP_COLOR
and DATABASE_URL (set per-container in docker-compose.yml) are the only
difference between the two at runtime.
"""
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from redis import Redis
from rq import Queue

from app.config import get_settings
from app.db import dict_cursor, raw_connection, tenant_connection
from app.security import require_role, require_tenant_match

settings = get_settings()
app = FastAPI(title="blue-green-multitenant-lab")
_redis = Redis.from_url(settings.redis_url)
# One Redis instance, one queue per color: a job enqueued by the blue app
# must only ever be picked up by a worker whose DATABASE_URL points at
# db-blue, or it would write a tenant's row into the wrong side's Postgres.
_queue = Queue(f"ai-jobs-{settings.app_color}", connection=_redis)


@app.get("/healthz")
def healthz():
    """Health gate target for the cutover script. Proves the app can reach
    its own Postgres, not just that the process is alive."""
    try:
        with raw_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - health check must report, not raise
        return {"status": "unhealthy", "color": settings.app_color, "error": str(exc)}, 503
    return {"status": "healthy", "color": settings.app_color, "version": settings.app_version}


def _tenant_by_slug(slug: str) -> dict:
    with raw_connection() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT id, slug, schema_name FROM public.tenants WHERE slug = %s", (slug,))
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown tenant")
    return row


class DocumentIn(BaseModel):
    title: str
    body: str = ""


@app.post("/tenants/{tenant_slug}/documents")
def create_document(tenant_slug: str, doc: DocumentIn, claims: dict = Depends(require_role("member"))):
    require_tenant_match(tenant_slug, claims)
    tenant = _tenant_by_slug(tenant_slug)
    doc_id = str(uuid.uuid4())
    with tenant_connection(tenant["schema_name"], tenant["id"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (id, tenant_id, title, body) VALUES (%s, %s, %s, %s)",
                (doc_id, tenant["id"], doc.title, doc.body),
            )
    return {"id": doc_id}


@app.get("/tenants/{tenant_slug}/documents")
def list_documents(
    tenant_slug: str,
    claims: dict = Depends(require_role("member")),
    _simulate_wrong_schema_bug: str | None = None,
):
    """`_simulate_wrong_schema_bug` exists ONLY so tests/test_isolation.py has
    a concrete application bug to exploit: it names another tenant's
    schema to query against (as if a cache or lookup bug resolved the
    wrong schema for this slug), while the RLS session variable
    (`app.tenant_id`) is still set from the caller's own verified tenant --
    it is derived from `tenant`, never from this parameter. This is the
    actual bug class the second isolation layer defends against: the wrong
    table got queried, but the security context did not silently follow it.
    A real endpoint would never take a schema name from a client at all;
    this parameter stands in for the bug being *already resolved wrong*
    inside the server, not for an attacker choosing it.
    """
    require_tenant_match(tenant_slug, claims)
    tenant = _tenant_by_slug(tenant_slug)

    if _simulate_wrong_schema_bug:
        # The bug: query the wrong schema by name, with no tenant_id
        # predicate at all -- as if the code trusted "I'm already looking
        # at the right tenant's schema" and skipped the belt-and-suspenders
        # WHERE clause below. app.tenant_id is still set from the caller's
        # own verified tenant (tenant["id"]), never from this parameter, so
        # RLS alone decides what comes back.
        query_schema = _simulate_wrong_schema_bug
        with tenant_connection(query_schema, tenant["id"]) as conn:
            with dict_cursor(conn) as cur:
                cur.execute("SELECT id, title, body, tenant_id FROM documents")
                rows = cur.fetchall()
        return {"documents": rows, "queried_schema": query_schema, "bug_simulated": True}

    with tenant_connection(tenant["schema_name"], tenant["id"]) as conn:
        with dict_cursor(conn) as cur:
            # Defense in depth: the app-level filter is correct here because
            # tenant["schema_name"] was looked up honestly, but RLS (layer 2)
            # is what makes the bug above harmless rather than this line.
            cur.execute("SELECT id, title, body, tenant_id FROM documents WHERE tenant_id = %s", (tenant["id"],))
            rows = cur.fetchall()
    return {"documents": rows, "queried_schema": tenant["schema_name"], "bug_simulated": False}


@app.delete("/tenants/{tenant_slug}/documents/{doc_id}")
def delete_document(tenant_slug: str, doc_id: str, claims: dict = Depends(require_role("admin"))):
    """Admin-only: a member token must be rejected here with 403 before any
    query runs, which is the RBAC guarantee tests/test_rbac.py checks --
    distinct from tenant isolation, which is a different failure mode
    (right role, wrong tenant) covered by tests/test_isolation.py."""
    require_tenant_match(tenant_slug, claims)
    tenant = _tenant_by_slug(tenant_slug)
    with tenant_connection(tenant["schema_name"], tenant["id"]) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s AND tenant_id = %s", (doc_id, tenant["id"]))
            deleted = cur.rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="unknown document")
    return {"deleted": doc_id}


class JobIn(BaseModel):
    prompt: str


@app.post("/tenants/{tenant_slug}/jobs")
def submit_job(tenant_slug: str, job: JobIn, claims: dict = Depends(require_role("member"))):
    """Enqueue a simulated AI-workload job. Backpressure: if the queue is
    already deeper than queue_max_depth, reject with 429 rather than let
    an unbounded burst pile up -- this is the mechanism the loadgen's burst
    mode is built to exercise."""
    require_tenant_match(tenant_slug, claims)
    tenant = _tenant_by_slug(tenant_slug)

    depth = _queue.count
    if depth >= settings.queue_max_depth:
        raise HTTPException(status_code=429, detail=f"queue depth {depth} exceeds backpressure limit")

    job_id = str(uuid.uuid4())
    with tenant_connection(tenant["schema_name"], tenant["id"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, tenant_id, prompt, status) VALUES (%s, %s, %s, 'queued')",
                (job_id, tenant["id"], job.prompt),
            )
    _queue.enqueue(
        "worker.tasks.process_job",
        job_id=job_id,
        tenant_slug=tenant_slug,
        schema_name=tenant["schema_name"],
        tenant_id=tenant["id"],
    )
    return {"id": job_id, "status": "queued", "queue_depth_at_submit": depth}


@app.get("/tenants/{tenant_slug}/jobs/{job_id}")
def get_job(tenant_slug: str, job_id: str, claims: dict = Depends(require_role("member"))):
    require_tenant_match(tenant_slug, claims)
    tenant = _tenant_by_slug(tenant_slug)
    with tenant_connection(tenant["schema_name"], tenant["id"]) as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                "SELECT id, status, result, prompt FROM jobs WHERE id = %s AND tenant_id = %s",
                (job_id, tenant["id"]),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return row


@app.get("/queue/depth")
def queue_depth():
    """Unauthenticated metrics endpoint the load generator polls to show
    backpressure kicking in during a burst."""
    return {"depth": _queue.count, "max_depth": settings.queue_max_depth}
