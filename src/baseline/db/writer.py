"""Writes parsed schedules into Postgres.

Two ideas make this safe to re-run:

1. IDs are deterministic. The same tenant + file always gets the same upload_id, and the
   same upload always gets the same snapshot_id (uuid5 instead of random uuid4).
2. Each write replaces itself. Before inserting a snapshot we delete any existing snapshot
   with that id (child rows go with it via ON DELETE CASCADE). Run it once or ten times,
   the result is the same.

Everything happens inside the caller's transaction, so a failure halfway through leaves
nothing behind.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from baseline.canonical.models import Calendar, ProgressReport, Schedule
from baseline.db.tables import table

NAMESPACE = uuid.UUID("6f1c2a52-6b0e-4d55-9a5e-3f1f4f6b7a10")


def upload_uuid(tenant_id: str, sha256: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"upload:{tenant_id}:{sha256}")


def snapshot_uuid(tenant_id: str, upload_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"snapshot:{tenant_id}:{upload_id}")


def ensure_tenant(conn: Connection, tenant_id: str, name: str, region: str) -> None:
    t = table("tenant")
    conn.execute(
        insert(t)
        .values(tenant_id=tenant_id, name=name, region=region)
        .on_conflict_do_update(index_elements=["tenant_id"], set_={"name": name, "region": region})
    )


def ensure_project(conn: Connection, tenant_id: str, project_id: str, name: str) -> None:
    t = table("project")
    conn.execute(
        insert(t)
        .values(tenant_id=tenant_id, project_id=project_id, name=name)
        .on_conflict_do_nothing()
    )


def latest_data_date(conn: Connection, project_id: str) -> date | None:
    s = table("snapshot")
    return conn.execute(
        select(func.max(s.c.data_date)).where(s.c.project_id == project_id)
    ).scalar()


def _calendar_rows(tenant_id: str, snapshot_id: uuid.UUID, cals: list[Calendar]) -> list[dict]:
    return [
        {
            "tenant_id": tenant_id,
            "snapshot_id": snapshot_id,
            "calendar_id": c.calendar_id,
            "name": c.name,
            "hours_per_day": c.hours_per_day,
            "work_days": c.work_days,
            "holidays": c.holidays,
        }
        for c in cals
    ]


def write_snapshot(
    conn: Connection, tenant_id: str, upload_id: uuid.UUID, schedule: Schedule
) -> uuid.UUID:
    """Save one schedule as one snapshot. Returns the snapshot id."""
    assert schedule.data_date is not None, "contracts guarantee a data date"
    snapshot_id = snapshot_uuid(tenant_id, upload_id)

    ensure_project(conn, tenant_id, schedule.project_id, schedule.project_name)
    conn.execute(delete(table("snapshot")).where(table("snapshot").c.snapshot_id == snapshot_id))

    conn.execute(
        table("snapshot").insert(),
        {
            "snapshot_id": snapshot_id,
            "tenant_id": tenant_id,
            "project_id": schedule.project_id,
            "upload_id": upload_id,
            "data_date": schedule.data_date,
            "activity_count": len(schedule.activities),
            "relationship_count": len(schedule.relationships),
        },
    )

    base = {"tenant_id": tenant_id, "snapshot_id": snapshot_id}
    conn.execute(
        table("activity").insert(),
        [
            {**base, **a.model_dump(mode="python", exclude={"is_milestone"})}
            for a in schedule.activities
        ],
    )
    if schedule.relationships:
        conn.execute(
            table("relationship").insert(),
            [{**base, **r.model_dump(mode="python")} for r in schedule.relationships],
        )
    if schedule.calendars:
        conn.execute(
            table("calendar").insert(),
            _calendar_rows(tenant_id, snapshot_id, schedule.calendars),
        )
    return snapshot_id


def write_progress(
    conn: Connection, tenant_id: str, upload_id: uuid.UUID, report: ProgressReport
) -> int:
    """Save the usable rows of a progress sheet. Rows without an activity ID are skipped."""
    pr = table("progress_report")
    conn.execute(delete(pr).where(pr.c.upload_id == upload_id))
    rows = [
        {
            "tenant_id": tenant_id,
            "upload_id": upload_id,
            "source_row": r.source_row,
            "project_id": report.project_id,
            "trade": report.trade,
            "activity_id": r.activity_id,
            "description": r.description,
            "reported_pct": r.reported_pct,
            "report_date": r.report_date,
            "remark": r.remark,
        }
        for r in report.rows
        if r.activity_id
    ]
    if rows:
        conn.execute(pr.insert(), rows)
    return len(rows)


def current_snapshots(conn: Connection, project_id: str) -> list[dict]:
    """Latest-known snapshot for each data date of a project, oldest first."""
    rows = conn.execute(
        text(
            "SELECT snapshot_id, data_date, ingested_at, upload_id FROM snapshot_current "
            "WHERE project_id = :p ORDER BY data_date"
        ),
        {"p": project_id},
    ).mappings()
    return [dict(r) for r in rows]