"""Step 6a: a file failed. Keep it, explain why, and never let it into the tables.

The raw copy stays where it is (raw files are never moved or deleted). We copy it into
the quarantine area so people can find rejected files in one place, and write one
quarantine_event row with the reason code, the rule, and a few sample rows.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import Connection, delete

from baseline.canonical.contracts import Violation
from baseline.db.tables import table
from baseline.db.writer import NAMESPACE
from baseline.ingest.dedupe import set_upload_status
from baseline.ingest.landing import LandedFile, ObjectStore, quarantine_key


def quarantine_upload(
    conn: Connection,
    store: ObjectStore,
    tenant_id: str,
    upload_id: uuid.UUID,
    landed: LandedFile,
    violation: Violation,
    *,
    project_id: str | None = None,
    source_format: str | None = None,
) -> None:
    qkey = quarantine_key(tenant_id, landed)
    if not store.exists(qkey):
        store.copy(landed.raw_key, qkey)

    qe = table("quarantine_event")
    # Re-processing the same upload replaces its old events instead of piling up new ones.
    conn.execute(delete(qe).where(qe.c.upload_id == upload_id))
    conn.execute(
        qe.insert(),
        {
            "event_id": uuid.uuid5(NAMESPACE, f"quarantine:{upload_id}:{violation.reason_code}"),
            "tenant_id": tenant_id,
            "upload_id": upload_id,
            "reason_code": violation.reason_code,
            "rule": violation.rule,
            "detail": violation.detail,
            # default=str turns dates and other objects into plain strings for JSON.
            "sample": json.loads(json.dumps(violation.sample, default=str)),
        },
    )
    set_upload_status(
        conn, upload_id, "quarantined", project_id=project_id, source_format=source_format
    )


def clear_quarantine(conn: Connection, upload_id: uuid.UUID) -> None:
    """Called when a previously rejected upload is loaded after all (e.g. --allow-backdated).

    The quarantine copy of the file is kept as history; only the open event is removed.
    """
    qe = table("quarantine_event")
    conn.execute(delete(qe).where(qe.c.upload_id == upload_id))