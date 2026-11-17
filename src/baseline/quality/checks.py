"""Data quality monitors. Each one returns a CheckResult; none of them raise.

These run after loads (and as Dagster asset checks) so problems are caught by a monitor
before a customer notices them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, text


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    metadata: dict = field(default_factory=dict)


def freshness(
    conn: Connection, project_id: str, max_age_days: int = 10, today: date | None = None
) -> CheckResult:
    """Has this project sent an update recently? Weekly updates → alert after 10 days."""
    today = today or date.today()
    latest = conn.execute(
        text("SELECT max(data_date) FROM snapshot WHERE project_id = :p"), {"p": project_id}
    ).scalar()
    if latest is None:
        return CheckResult("freshness", False, f"no snapshots for {project_id}")
    age = (today - latest).days
    return CheckResult(
        "freshness",
        age <= max_age_days,
        f"latest data date {latest} is {age} days old (limit {max_age_days})",
        {"latest_data_date": str(latest), "age_days": age},
    )


def volume(conn: Connection, project_id: str, max_change: float = 0.30) -> CheckResult:
    """Did the activity count jump or crash compared with the previous update?

    A schedule that suddenly loses 40% of its activities is usually a bad export
    (a filter left on in P6), not a real change in scope.
    """
    rows = conn.execute(
        text(
            "SELECT data_date, activity_count FROM snapshot_current "
            "WHERE project_id = :p ORDER BY data_date DESC LIMIT 2"
        ),
        {"p": project_id},
    ).all()
    if len(rows) < 2:
        return CheckResult("volume", True, "fewer than two snapshots; nothing to compare")
    (cur_date, cur), (_, prev) = rows
    change = (cur - prev) / prev if prev else 0.0
    return CheckResult(
        "volume",
        abs(change) <= max_change,
        f"{cur} activities on {cur_date} vs {prev} before ({change:+.0%}, limit ±{max_change:.0%})",
        {"current": cur, "previous": prev, "change": round(change, 4)},
    )


def referential_integrity(conn: Connection, project_id: str) -> CheckResult:
    """Every relationship in the database must point at activities in the same snapshot."""
    orphans = conn.execute(
        text(
            """
            SELECT count(*)
            FROM relationship r
            JOIN snapshot s ON s.snapshot_id = r.snapshot_id
            WHERE s.project_id = :p
              AND (NOT EXISTS (SELECT 1 FROM activity a
                               WHERE a.snapshot_id = r.snapshot_id
                                 AND a.activity_id = r.predecessor_id)
                OR NOT EXISTS (SELECT 1 FROM activity a
                               WHERE a.snapshot_id = r.snapshot_id
                                 AND a.activity_id = r.successor_id))
            """
        ),
        {"p": project_id},
    ).scalar()
    return CheckResult(
        "referential_integrity",
        orphans == 0,
        f"{orphans} relationships point at missing activities",
        {"orphans": orphans},
    )


def quarantine_rate(conn: Connection, max_rate: float = 0.20) -> CheckResult:
    """If lots of files are being rejected, a customer's export settings probably changed."""
    total, bad = conn.execute(
        text("SELECT count(*), count(*) FILTER (WHERE status = 'quarantined') FROM upload")
    ).one()
    rate = bad / total if total else 0.0
    return CheckResult(
        "quarantine_rate",
        rate <= max_rate,
        f"{bad} of {total} uploads quarantined ({rate:.0%}, limit {max_rate:.0%})",
        {"total": total, "quarantined": bad},
    )


def run_all(conn: Connection, project_id: str, today: date | None = None) -> list[CheckResult]:
    return [
        freshness(conn, project_id, today=today),
        volume(conn, project_id),
        referential_integrity(conn, project_id),
        quarantine_rate(conn),
    ]