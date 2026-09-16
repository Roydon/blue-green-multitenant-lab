#!/usr/bin/env python3
"""Reads Locust's --csv stats output and exits non-zero if the Aggregated
row shows any failed requests. Used by scripts/run_demo.sh instead of
guessing at column positions, which shift between Locust versions.
"""
import csv
import sys


def main(stats_path: str) -> int:
    with open(stats_path, newline="") as f:
        rows = list(csv.DictReader(f))

    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if agg is None:
        print(f"no 'Aggregated' row found in {stats_path}", file=sys.stderr)
        return 2

    requests = int(agg["Request Count"])
    failures = int(agg["Failure Count"])
    print(f"requests={requests} failures={failures}")
    if failures != 0:
        print(f"FAIL: {failures} failed request(s) recorded during the run", file=sys.stderr)
        return 1
    if requests == 0:
        print("FAIL: zero requests recorded -- load generator did not run", file=sys.stderr)
        return 1
    print("PASS: zero failed requests")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "demo-output/locust_stats_stats.csv"))
