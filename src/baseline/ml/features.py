"""Run features.sql for one project and one date, and optionally save the result."""

from __future__ import annotations

from datetime import date
from importlib import resources

import pandas as pd
from sqlalchemy import Connection, text

FEATURE_COLUMNS = [
    "total_float_days",
    "float_change_4w",
    "min_float_8w",
    "percent_complete",
    "progress_velocity_4w",
    "remaining_duration_days",
    "finish_slip_count",
    "finish_slip_days",
    "pred_count",
    "late_pred_count",
    "is_critical",
    "weeks_tracked",
    "has_delay_note",
    "is_milestone",
]


def feature_sql() -> str:
    return resources.files("baseline.ml").joinpath("features.sql").read_text()


def compute_features(conn: Connection, project_id: str, as_of: date) -> pd.DataFrame:
    rows = conn.execute(text(feature_sql()), {"project_id": project_id, "as_of": as_of})
    df = pd.DataFrame(rows.mappings().all(), columns=["activity_id", *FEATURE_COLUMNS])
    df[FEATURE_COLUMNS] = df[FEATURE_COLUMNS].astype("float64")
    return df


def store_features(
    conn: Connection, tenant_id: str, project_id: str, as_of: date, df: pd.DataFrame
) -> int:
    conn.execute(
        text("DELETE FROM activity_feature WHERE project_id = :p AND as_of_date = :d"),
        {"p": project_id, "d": as_of},
    )
    if df.empty:
        return 0
    records = df.astype(object).where(df.notna(), None).to_dict("records")
    cols = ["activity_id", *FEATURE_COLUMNS]
    conn.execute(
        text(
            f"INSERT INTO activity_feature (tenant_id, project_id, as_of_date, {', '.join(cols)}) "
            f"VALUES (:tenant_id, :project_id, :as_of_date, {', '.join(':' + c for c in cols)})"
        ),
        [{**r, "tenant_id": tenant_id, "project_id": project_id, "as_of_date": as_of}
         for r in records],
    )  # fmt: skip
    return len(records)