"""The job function RQ workers execute. Simulates AI-workload latency
(configurable via JOB_LATENCY_SECONDS) so the burst load generator has
something realistic to queue up against.
"""
import time

from app.config import get_settings
from app.db import tenant_connection


def process_job(job_id: str, tenant_slug: str, schema_name: str, tenant_id: str):
    settings = get_settings()
    with tenant_connection(schema_name, tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (job_id,))

    time.sleep(settings.job_latency_seconds)  # stand-in for an AI inference call

    with tenant_connection(schema_name, tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'done', result = %s, completed_at = now() WHERE id = %s",
                (f"processed:{tenant_slug}:{job_id}", job_id),
            )
    return {"job_id": job_id, "status": "done"}
