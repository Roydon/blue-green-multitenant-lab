# blue-green-multitenant-lab

A reference implementation of a blue/green deployment cutover plus
enterprise multi-tenant SaaS isolation, built as a scoped technical
demonstration. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the
full blueprint: tier dependency map, cutover mechanics, isolation model,
and auth/RBAC design.

## What's here

- **Blue/green cutover**: two identical app stacks behind an NGINX router,
  each with its own Postgres (green kept current via logical replication),
  health-gated and load-generator-verified traffic shift with automatic
  rollback (`scripts/cutover.sh`).
- **Multi-tenant isolation**: schema-per-tenant plus forced row-level
  security as an independent second layer, with a test that proves a
  tenant-A session cannot read tenant-B data even when the app queries the
  wrong schema (`tests/test_isolation.py`).
- **OIDC + RBAC**: a mock Auth0/Clerk-shaped provider issuing signed,
  tenant-scoped JWTs, enforced by role- and tenant-matching middleware
  (`tests/test_rbac.py`).
- **Redis-backed job queue**: simulated AI-workload jobs, worker pool
  replicas, and burst backpressure (`loadgen/burst_load.py`).

## Running it

Requires Docker with the Compose plugin, and Python 3.12+ on the host for
the load generator and helper scripts.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```bash
make demo   # full cutover with a live load generator; fails on any dropped request
make test   # tenant isolation + RBAC test suites
```

`make demo` output (Locust stats, logs) lands in `demo-output/`, which is
gitignored.

## Stack

FastAPI, SQLAlchemy-style config over raw `psycopg2` (RLS session variables
need `SET LOCAL`, which the ORM layer doesn't expose cleanly), Postgres 16,
Redis + RQ, `python-jose` for the mock OIDC/JWT provider, Locust for the
load-gen proof, pytest for the isolation/RBAC suites.
