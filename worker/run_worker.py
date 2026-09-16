"""RQ worker entrypoint. Multiple replicas of this process (see
docker-compose.yml worker-pool) share the same Redis queue, so scaling the
pool is just adding replicas -- the burst load generator proves that queue
depth, not per-worker capacity, is what backpressure in app/main.py guards.
"""
from redis import Redis
from rq import Queue, Worker

from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    conn = Redis.from_url(settings.redis_url)
    queue_name = f"ai-jobs-{settings.app_color}"
    worker = Worker([Queue(queue_name, connection=conn)], connection=conn)
    worker.work(with_scheduler=False)
