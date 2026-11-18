"""Training labels: did this activity's forecast finish slip within the next N weeks?

slipped = 1 if, in the update N weeks later, the activity's forecast finish (from our own
CPM) is more than `tolerance_days` later than it was on the as_of date.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import Connection, text

from baseline.db.reader import snapshot_on_or_before


def compute_labels(
    conn: Connection,
    project_id: str,
    as_of: date,
    horizon_weeks: int = 4,
    tolerance_days: int = 5,
) -> pd.DataFrame | None:
    """Returns None when there is no update far enough in the future to judge."""
    now = snapshot_on_or_before(conn, project_id, as_of)
    target = as_of + timedelta(weeks=horizon_weeks)
    later = snapshot_on_or_before(conn, project_id, target)
    if not now or not later or (target - later["data_date"]).days > 7:
        return None

    rows = conn.execute(
        text(
            """
            SELECT a.activity_id,
                   (c2.early_finish - c1.early_finish) AS moved_days
            FROM activity a
            JOIN activity_cpm c1 ON c1.snapshot_id = a.snapshot_id
                                AND c1.activity_id = a.activity_id
            JOIN activity_cpm c2 ON c2.snapshot_id = :later
                                AND c2.activity_id = a.activity_id
            WHERE a.snapshot_id = :now AND a.status <> 'complete'
            """
        ),
        {"now": now["snapshot_id"], "later": later["snapshot_id"]},
    ).all()
    df = pd.DataFrame(rows, columns=["activity_id", "moved_days"])
    df["slipped"] = (df["moved_days"] > tolerance_days).astype(int)
    return df[["activity_id", "slipped"]]