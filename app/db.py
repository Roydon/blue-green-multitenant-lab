"""Per-request Postgres connections that carry the tenant context.

Two isolation layers are enforced on every query issued through this module:
  1. search_path is pinned to the tenant's own schema for the connection.
  2. app.tenant_id is set via SET LOCAL so Postgres RLS policies
     (see db/init/01-schema.sql) can filter rows even if application code
     forgets a WHERE clause -- the bug tests/test_isolation.py exploits.
"""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.sql

from app.config import get_settings


def _dsn() -> str:
    # psycopg2 wants a plain DSN, not the SQLAlchemy-style URL in Settings.
    url = get_settings().database_url
    return url.replace("postgresql+psycopg2://", "postgresql://")


@contextmanager
def tenant_connection(schema_name: str, tenant_id: str):
    """Yield a connection scoped to one tenant for the life of the `with` block."""
    conn = psycopg2.connect(_dsn())
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(psycopg2.sql.SQL("SET search_path TO {}, public").format(
                psycopg2.sql.Identifier(schema_name)
            ))  # nosec: identifier is quoted via psycopg2.sql, not string-formatted
            # SET LOCAL: scoped to this transaction only, never leaks across
            # pooled/reused connections.
            cur.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def raw_connection():
    """Unscoped connection for reading the public.tenants registry only."""
    conn = psycopg2.connect(_dsn())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
