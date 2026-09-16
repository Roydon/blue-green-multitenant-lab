"""Fires a configurable burst of AI-workload job submissions concurrently
against the router, to demonstrate the Redis-backed queue absorbing a spike
and the app's backpressure (HTTP 429 above QUEUE_MAX_DEPTH) kicking in
rather than the queue growing unbounded.

Usage: python3 loadgen/burst_load.py [--count 800] [--concurrency 40]
"""
import argparse
import asyncio
import sys

import httpx

ROUTER_URL = "http://localhost:8080"
OIDC_TOKEN_URL = "http://localhost:9000/token"
TENANT_SLUG = "acme"


async def get_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        OIDC_TOKEN_URL,
        json={"username": "alice@tenant-a.test", "password": "anything"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def submit_one(client: httpx.AsyncClient, token: str, sem: asyncio.Semaphore, idx: int) -> str:
    async with sem:
        try:
            resp = await client.post(
                f"{ROUTER_URL}/tenants/{TENANT_SLUG}/jobs",
                json={"prompt": f"burst job {idx}"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            return f"error:{exc}"
        return str(resp.status_code)


async def main(count: int, concurrency: int) -> int:
    async with httpx.AsyncClient() as client:
        token = await get_token(client)
        sem = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(*(submit_one(client, token, sem, i) for i in range(count)))

    depth_resp = httpx.get(f"{ROUTER_URL}/queue/depth")
    tally: dict[str, int] = {}
    for r in results:
        tally[r] = tally.get(r, 0) + 1

    print(f"submitted {count} jobs at concurrency {concurrency}")
    print(f"result tally: {tally}")
    print(f"queue depth after burst: {depth_resp.json()}")

    accepted = tally.get("200", 0)
    rejected = tally.get("429", 0)
    print(f"accepted={accepted} rejected_with_backpressure={rejected}")

    if rejected == 0:
        print(
            "FAIL: burst never triggered backpressure (0 requests got 429) -- "
            "either QUEUE_MAX_DEPTH is higher than expected, workers drained faster "
            "than the burst arrived, or --count/--concurrency need to be higher",
            file=sys.stderr,
        )
        return 1
    print("PASS: backpressure triggered")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=800)
    parser.add_argument("--concurrency", type=int, default=40)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.count, args.concurrency)))
