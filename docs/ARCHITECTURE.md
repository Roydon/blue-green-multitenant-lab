# Architecture blueprint

This document is the blueprint format referenced in the source job brief: a
dependency map of the system's tiers, the migration/cutover mechanics, and
the multi-tenant isolation and auth model. It describes what this
repository actually builds and proves, not a hypothetical target system --
the intent is to demonstrate the blueprint format a real engagement would
produce for the client's own stack.

## 1. Tier dependency map

```
                          +-------------------+
                          |     Client        |
                          +---------+---------+
                                    |
                                    v
                          +-------------------+
                          |  Router (NGINX)   |  <- single entrypoint, :8080
                          |  weighted upstream|
                          +----+---------+----+
                               |         |
                     blue_weight%   green_weight%
                               |         |
                    +----------v--+   +--v----------+
                    |  app-blue   |   |  app-green  |   <- identical image,
                    |  (FastAPI)  |   |  (FastAPI)  |      differ only by
                    +--+------+--+   +--+------+---+      APP_COLOR / DATABASE_URL
                       |      |         |      |
             DB conn   |      | enqueue |      | enqueue     both trust one
                       |      |         |      |             external IdP:
                    +--v--+   |      +--v--+   |          +--------------+
                    | db- |   |      | db- |   |    <----+|  oidc (mock  |
                    |blue |   |      |green|   |          |  Auth0/Clerk)|
                    +--+--+   |      +--+--+   |          +--------------+
                       |      |         |      |
                       | logical repl.  |      |
                       +---------------->      |
                       (blue_pub -> green_sub)  |
                              |                 |
                        +-----v-----+     +-----v-----+
                        |worker-blue|     |worker-green|   <- RQ workers,
                        | (replicas)|     | (replicas) |      one queue per
                        +-----+-----+     +-----+-----+      color
                              |                 |
                        ai-jobs-blue      ai-jobs-green
                              \_______   _______/
                                      \ /
                                  +----v----+
                                  |  redis  |   <- one broker, two logical
                                  +---------+      queues (see section 3)
```

Dependency notes a migration blueprint needs to call out explicitly:

- **Router -> apps**: the only hard dependency a client-facing consumer has.
  Everything below it can change color without the client noticing, which
  is the entire point of blue/green.
- **App -> its own Postgres**: never cross-color. app-blue never talks to
  db-green and vice versa; this is enforced by `DATABASE_URL` being
  container-scoped, not by application logic, so there's no code path that
  could accidentally cross it.
- **db-blue -> db-green**: one-way logical replication, not a shared
  database. This is what lets §2's cutover gate on "has green actually
  caught up" instead of on a fixed sleep.
- **App -> oidc**: both colors trust the same issuer. If this dependency
  broke during a real migration (e.g. issuer URL changed), both colors
  would fail identically -- which is why it's drawn as one shared node, not
  duplicated per color.
- **App -> Redis -> worker**: fire-and-forget. A worker outage degrades job
  *latency*, not the API's ability to accept requests, which is exactly
  the isolation a job queue is meant to buy for spiky AI workloads.

## 2. Blue/green cutover mechanics

**State before cutover**: db-blue is the live writer. A logical replication
subscription (`db/replication/setup.sh`) streams every committed row from
db-blue to db-green continuously, so green is never "cold" -- it is warm,
serving no traffic, but current.

**Cutover** (`scripts/cutover.sh`), in order:

1. **Health gate** -- `curl` directly against `app-green:8000/healthz`,
   bypassing the router. `/healthz` proves Postgres reachability, not just
   process liveness (see `app/main.py`). A failure here aborts before any
   traffic is touched, no rollback needed because nothing moved yet.
2. **Replication gate** -- compares `db-blue`'s current WAL LSN against
   `db-green`'s subscription lag (`pg_wal_lsn_diff`). Below a 1 MiB
   threshold, green is considered caught up. This is the step a naive
   blue/green demo skips, and skipping it is what actually causes dropped
   or duplicated data during a real cutover: if green serves a read one
   transaction behind blue, that's a silent correctness bug, not an outage,
   which is worse.
3. **Graduated traffic shift** -- `router/nginx.conf`'s upstream weights
   move blue/green through 90/10 -> 50/50 -> 0/100, rewritten by
   `scripts/render_upstream.py` and applied with `nginx -s reload` (not a
   container restart) at each step. A reload lets NGINX finish in-flight
   connections on the old worker processes while new connections go to the
   new config -- this is the specific mechanism that makes "zero dropped
   requests" possible rather than aspirational.
4. **Health probe per step** -- several requests through the *router*
   (not directly to green) after each reload. Any failure triggers
   immediate rollback to 100% blue / 0% green, also via reload, and the
   script exits non-zero.
5. **Proof of zero drops** -- `scripts/run_demo.sh` runs a Locust load
   generator continuously against the router for the whole cutover window,
   independent of the cutover script's own probes. `make demo` fails
   unless Locust's own aggregated stats report exactly zero failed
   requests.

**Rollback** is the same mechanism as the forward shift (rewrite weights,
reload) run in the opposite direction, which is why it's fast and doesn't
require redeploying anything.

## 3. Redis-backed job queue and backpressure

One Redis instance brokers two logical queues, `ai-jobs-blue` and
`ai-jobs-green`, so a job enqueued by one color's API can only ever be
picked up by a worker connected to that same color's Postgres (`worker/
run_worker.py`). `worker-blue`/`worker-green` each run as a replica set
(`deploy.replicas: 2`), so absorbing a burst is "add more replicas",
not "make each worker faster."

Backpressure lives in `app/main.py`'s `submit_job`: once `Queue.count`
exceeds `QUEUE_MAX_DEPTH` (default 150), new submissions are rejected with
`429` instead of being queued indefinitely. `loadgen/burst_load.py`
demonstrates this directly by firing a concurrent burst of job submissions
and reporting the accepted/429 split plus the queue depth afterward.

## 4. Multi-tenant isolation: two independent layers

1. **Schema-per-tenant** -- `db/init/01-schema.sql`'s `provision_tenant()`
   function creates one Postgres schema per tenant (`tenant_<slug>`), with
   its own `documents` and `jobs` tables. This is provisioned identically
   on **both** db-blue and db-green (see `app/seed.py`), independent of the
   row-level replication in §2 -- structure is provisioned on each side,
   data flows one-way.
2. **Row-level security (forced)** -- every tenant table has
   `FORCE ROW LEVEL SECURITY` with a policy keyed on
   `current_setting('app.tenant_id')`, which `app/db.py` sets via
   `SET LOCAL` from the caller's verified JWT claim, never from request
   input. `FORCE` matters specifically because the `app_user` role also
   owns the tables (it has to, to run migrations); without `FORCE`, the
   owner role bypasses RLS entirely and the policy would be decorative.

The two layers defend against different failure modes: schema-per-tenant
is what a competent-but-rushed developer relies on ("I only query my own
tenant's schema"); RLS is what catches the case where that assumption
breaks -- a stale cache, a wrong lookup, a copy-pasted query -- and it is
this second layer that `tests/test_isolation.py` actually exercises, by
deliberately pointing a correctly-authenticated session at the *wrong*
tenant's schema and confirming RLS still returns nothing.

`app_user`, the role every query runs as, is granted `USAGE` on every
tenant schema (a common and intentional simplification in schema-per-tenant
systems: schema *access* is not the isolation boundary, RLS is) and is
explicitly `NOSUPERUSER NOBYPASSRLS`, so there is no privilege escape
hatch available to application code.

## 5. Auth: mock OIDC provider + RBAC middleware

`app/oidc_provider.py` is a minimal Auth0/Clerk-shaped OIDC provider: it
publishes a JWKS endpoint, signs tokens with RS256, and issues claims
(`tenant`, `role`) the same way a real IdP's custom-claims/rules feature
would. Both app-blue and app-green verify tokens against this same JWKS
(`app/security.py`) rather than trusting an unsigned header, which is the
difference between "RBAC" and "an honor system."

Enforcement happens in two independent checks per request:

- `require_role(minimum)` -- rejects with `403` if the token's role rank is
  below what the endpoint needs (e.g. `DELETE` requires `admin`, everything
  else requires `member`).
- `require_tenant_match(tenant_slug, claims)` -- rejects with `403` if the
  token's `tenant` claim doesn't match the tenant in the URL, regardless of
  role. An admin token for tenant A must not act as an admin on tenant B.

`tests/test_rbac.py` exercises the first axis (role rank, fixed tenant);
`tests/test_isolation.py` exercises the second (tenant boundary, fixed or
irrelevant role) and the RLS layer underneath it. Keeping these as separate
test files mirrors the two independent things that can go wrong in a real
multi-tenant system: the wrong person doing an action, and the right person
seeing the wrong tenant's data.

## 6. What a real migration blueprint adds on top of this

This lab fixes scope to what's demonstrable in a few hours: one region, one
router, two colors, mock auth. A production blueprint for the client's
actual system would extend the same dependency-map + gate + rollback
structure with: a DNS/CDN tier in front of the router, secrets management
for the OIDC signing key (currently generated in-process and lost on
restart -- fine for a demo, not for production), Alembic-based schema
migrations run through an expand/contract discipline against the
replication link in §2, and a real external IdP (Auth0/Clerk) issuing the
claims this repo's mock provider stands in for.
