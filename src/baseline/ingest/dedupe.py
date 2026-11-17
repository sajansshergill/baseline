"""Step 2: have we seen these exact bytes from this tenant before?

The database enforces it: upload has UNIQUE (tenant_id, content_sha256). We try to insert,
and if the row already exists we get the existing one back instead.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert

from baseline.db.tables import table
from baseline.db.writer import upload_uuid
from baseline.ingest.landing import LandedFile


@dataclass(frozen=True)
class UploadRecord:
    upload_id: uuid.UUID
    status: str  # received | loaded | quarantined
    is_new: bool


def register_upload(conn: Connection, tenant_id: str, landed: LandedFile) -> UploadRecord:
    up = table("upload")
    upload_id = upload_uuid(tenant_id, landed.sha256)
    inserted = conn.execute(
        insert(up)
        .values(
            upload_id=upload_id,
            tenant_id=tenant_id,
            filename=landed.filename,
            content_sha256=landed.sha256,
            storage_key=landed.raw_key,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "content_sha256"])
        .returning(up.c.upload_id)
    ).scalar()
    if inserted is not None:
        return UploadRecord(upload_id=inserted, status="received", is_new=True)

    row = conn.execute(
        select(up.c.upload_id, up.c.status).where(up.c.content_sha256 == landed.sha256)
    ).one()
    return UploadRecord(upload_id=row.upload_id, status=row.status, is_new=False)


def lock_upload(conn: Connection, upload_id: uuid.UUID) -> str:
    """Lock the upload row for this transaction and return its status.

    If two workers pick up the same file at once, the second one waits here, then sees
    status 'loaded' and stops. That's what keeps concurrent runs from doubling data.
    """
    up = table("upload")
    return conn.execute(
        select(up.c.status).where(up.c.upload_id == upload_id).with_for_update()
    ).scalar_one()


def set_upload_status(
    conn: Connection,
    upload_id: uuid.UUID,
    status: str,
    *,
    project_id: str | None = None,
    source_format: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    up = table("upload")
    values: dict = {"status": status}
    if project_id is not None:
        values["project_id"] = project_id
    if source_format is not None:
        values["source_format"] = source_format
    if warnings is not None:
        values["warnings"] = json.loads(json.dumps(warnings))
    conn.execute(update(up).where(up.c.upload_id == upload_id).values(**values))