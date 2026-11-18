"""End-to-end ingest: land → dedupe → parse → validate → write (or quarantine).

This is the glue the CLI (`baseline load`) and the inbox sensor both call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from baseline.canonical.contracts import (
    Violation,
    check_data_date_order,
    validate_progress,
    validate_schedule,
)
from baseline.canonical.models import ProgressReport, Schedule
from baseline.db.session import tenant_connection
from baseline.db.writer import latest_data_date, write_progress, write_snapshot
from baseline.ingest.dedupe import lock_upload, register_upload, set_upload_status
from baseline.ingest.landing import ObjectStore, get_store, land_file
from baseline.ingest.quarantine import clear_quarantine, quarantine_upload
from baseline.ingest.router import parse_file
from baseline.parsers.base import ParseError

SCHEDULE_EXTENSIONS = {".xer", ".xml"}
PROGRESS_EXTENSIONS = {".xlsx"}
ALL_EXTENSIONS = SCHEDULE_EXTENSIONS | PROGRESS_EXTENSIONS


@dataclass
class IngestResult:
    outcome: str  # loaded | duplicate | quarantined
    filename: str
    data_date: date | None = None
    reason_code: str | None = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)


def ingest_bytes(
    tenant_id: str,
    filename: str,
    data: bytes,
    *,
    store: ObjectStore | None = None,
    allow_backdated: bool = False,
    reprocess: bool = False,
) -> IngestResult:
    """Ingest one file's bytes. Always lands the original; never half-loads."""
    store = store or get_store()
    landed = land_file(store, tenant_id, filename, data)

    with tenant_connection(tenant_id) as conn:
        upload = register_upload(conn, tenant_id, landed)
        status = lock_upload(conn, upload.upload_id)

        if status == "loaded" and not reprocess:
            return IngestResult(outcome="duplicate", filename=landed.filename)

        try:
            parsed = parse_file(data, landed.filename)
        except ParseError as exc:
            quarantine_upload(
                conn,
                store,
                tenant_id,
                upload.upload_id,
                landed,
                Violation(exc.reason_code, "parseable", exc.detail),
            )
            return IngestResult(
                outcome="quarantined",
                filename=landed.filename,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )

        if isinstance(parsed, Schedule):
            validation = validate_schedule(parsed)
            if not validation.ok:
                v = validation.first
                assert v is not None
                quarantine_upload(
                    conn,
                    store,
                    tenant_id,
                    upload.upload_id,
                    landed,
                    v,
                    project_id=parsed.project_id,
                    source_format=parsed.source_format,
                )
                return IngestResult(
                    outcome="quarantined",
                    filename=landed.filename,
                    data_date=parsed.data_date,
                    reason_code=v.reason_code,
                    detail=v.detail,
                    warnings=validation.warnings,
                )

            assert parsed.data_date is not None
            if not allow_backdated:
                regression = check_data_date_order(
                    parsed.data_date, latest_data_date(conn, parsed.project_id)
                )
                if regression is not None:
                    quarantine_upload(
                        conn,
                        store,
                        tenant_id,
                        upload.upload_id,
                        landed,
                        regression,
                        project_id=parsed.project_id,
                        source_format=parsed.source_format,
                    )
                    return IngestResult(
                        outcome="quarantined",
                        filename=landed.filename,
                        data_date=parsed.data_date,
                        reason_code=regression.reason_code,
                        detail=regression.detail,
                        warnings=validation.warnings,
                    )

            write_snapshot(conn, tenant_id, upload.upload_id, parsed)
            set_upload_status(
                conn,
                upload.upload_id,
                "loaded",
                project_id=parsed.project_id,
                source_format=parsed.source_format,
                warnings=validation.warnings,
            )
            clear_quarantine(conn, upload.upload_id)
            return IngestResult(
                outcome="loaded",
                filename=landed.filename,
                data_date=parsed.data_date,
                warnings=validation.warnings,
            )

        assert isinstance(parsed, ProgressReport)
        validation = validate_progress(parsed)
        if not validation.ok:
            v = validation.first
            assert v is not None
            quarantine_upload(
                conn,
                store,
                tenant_id,
                upload.upload_id,
                landed,
                v,
                project_id=parsed.project_id,
                source_format=parsed.source_format,
            )
            return IngestResult(
                outcome="quarantined",
                filename=landed.filename,
                reason_code=v.reason_code,
                detail=v.detail,
                warnings=validation.warnings,
            )

        write_progress(conn, tenant_id, upload.upload_id, parsed)
        set_upload_status(
            conn,
            upload.upload_id,
            "loaded",
            project_id=parsed.project_id,
            source_format=parsed.source_format,
            warnings=validation.warnings,
        )
        clear_quarantine(conn, upload.upload_id)
        return IngestResult(
            outcome="loaded",
            filename=landed.filename,
            warnings=validation.warnings,
        )


def ingest_path(
    tenant_id: str,
    path: Path,
    *,
    allow_backdated: bool = False,
    reprocess: bool = False,
    store: ObjectStore | None = None,
) -> list[IngestResult]:
    """Ingest a file or every schedule/progress file under a folder."""
    path = Path(path)
    store = store or get_store()
    if path.is_file():
        files = [path]
    else:
        files = sorted(
            p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in ALL_EXTENSIONS
        )
    return [
        ingest_bytes(
            tenant_id,
            f.name,
            f.read_bytes(),
            store=store,
            allow_backdated=allow_backdated,
            reprocess=reprocess,
        )
        for f in files
    ]
