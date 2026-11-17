"""Fingerprint table contents, to prove that re-running a pipeline changes nothing.

We hash every row (sorted by primary key) for the current tenant. Audit timestamps like
ingested_at are skipped: they record *when* a row was written, which is expected to differ
between runs, while the data itself must not.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import Connection, select

from baseline.db.tables import table

VOLATILE_COLUMNS = {"ingested_at", "received_at", "created_at"}

FINGERPRINT_TABLES = [
    "project",
    "upload",
    "snapshot",
    "activity",
    "relationship",
    "calendar",
    "activity_cpm",
    "progress_report",
    "quarantine_event",
]


def table_fingerprint(conn: Connection, name: str) -> str:
    t = table(name)
    cols = [c for c in t.columns if c.name not in VOLATILE_COLUMNS]
    order = list(t.primary_key.columns) or cols
    digest = hashlib.sha256()
    for row in conn.execute(select(*cols).order_by(*order)):
        digest.update(json.dumps(list(row), default=str).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def tenant_fingerprint(conn: Connection, tables: list[str] | None = None) -> dict[str, str]:
    """Fingerprint of every table, as seen by the tenant on this connection (RLS applies)."""
    return {name: table_fingerprint(conn, name) for name in tables or FINGERPRINT_TABLES}