"""Database connections.

The one rule: every connection that touches tenant data goes through tenant_connection().
It opens a transaction and tells Postgres which tenant is asking, so row-level security
filters every query automatically.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, text

from baseline.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@contextmanager
def tenant_connection(tenant_id: str, engine: Engine | None = None) -> Iterator[Connection]:
    """Yields a connection inside one transaction, scoped to one tenant.

    Commits when the block finishes, rolls back if it raises.
    `set_config(..., true)` means the setting only lasts for this transaction, so a
    pooled connection can never leak one tenant's id into the next request.
    """
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        yield conn