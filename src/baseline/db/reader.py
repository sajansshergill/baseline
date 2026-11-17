"""Read snapshots back out of Postgres as canonical Schedules.

Analytics, the CLI, and the dashboard all work on Schedule objects, so they don't care
whether data came straight from a parser or from the database.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Connection, select, text

from baseline.canonical.models import Activity, Calendar, Relationship, Schedule
from baseline.db.tables import table


def read_schedule(conn: Connection, snapshot_id: uuid.UUID) -> Schedule:
    snap = conn.execute(
        text(
            "SELECT s.project_id, p.name, s.data_date, u.source_format "
            "FROM snapshot s JOIN project p USING (tenant_id, project_id) "
            "JOIN upload u ON u.upload_id = s.upload_id WHERE s.snapshot_id = :s"
        ),
        {"s": snapshot_id},
    ).one()

    act_t, rel_t, cal_t = table("activity"), table("relationship"), table("calendar")
    skip = {"tenant_id", "snapshot_id"}
    activities = [
        Activity(**{k: v for k, v in row.items() if k not in skip})
        for row in conn.execute(
            select(act_t).where(act_t.c.snapshot_id == snapshot_id).order_by(act_t.c.activity_id)
        ).mappings()
    ]
    relationships = [
        Relationship(**{k: v for k, v in row.items() if k not in skip})
        for row in conn.execute(select(rel_t).where(rel_t.c.snapshot_id == snapshot_id)).mappings()
    ]
    calendars = [
        Calendar(**{k: v for k, v in row.items() if k not in skip})
        for row in conn.execute(select(cal_t).where(cal_t.c.snapshot_id == snapshot_id)).mappings()
    ]
    return Schedule(
        project_id=snap.project_id,
        project_name=snap.name,
        data_date=snap.data_date,
        source_format=snap.source_format or "unknown",
        activities=activities,
        relationships=relationships,
        calendars=calendars,
    )


def snapshot_on_or_before(conn: Connection, project_id: str, as_of: date) -> dict | None:
    """The latest-known snapshot with a data date on or before as_of."""
    row = conn.execute(
        text(
            "SELECT snapshot_id, data_date FROM snapshot_current "
            "WHERE project_id = :p AND data_date <= :d ORDER BY data_date DESC LIMIT 1"
        ),
        {"p": project_id, "d": as_of},
    ).mappings().first()
    return dict(row) if row else None


def list_projects(conn: Connection) -> list[str]:
    return list(conn.execute(text("SELECT project_id FROM project ORDER BY project_id")).scalars())