"""Score the current update of a project: which activities are likely to slip next?"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import Connection, text

from baseline.db.reader import snapshot_on_or_before
from baseline.ml.features import FEATURE_COLUMNS, compute_features
from baseline.ml.train import latest_data_date, load_model


def predict(
    conn: Connection, tenant_id: str, project_id: str, as_of: date | None = None
) -> pd.DataFrame:
    as_of = as_of or latest_data_date(conn, project_id)
    if as_of is None:
        raise ValueError(f"project {project_id} has no snapshots")
    booster, meta = load_model(tenant_id)
    version = meta["model_version"]

    feats = compute_features(conn, project_id, as_of)
    if feats.empty:
        return feats.assign(slip_probability=[])
    feats["slip_probability"] = booster.predict(feats[FEATURE_COLUMNS])

    conn.execute(
        text(
            "DELETE FROM slip_prediction "
            "WHERE project_id = :p AND as_of_date = :d AND model_version = :v"
        ),
        {"p": project_id, "d": as_of, "v": version},
    )
    conn.execute(
        text(
            "INSERT INTO slip_prediction "
            "(tenant_id, project_id, as_of_date, activity_id, model_version, slip_probability) "
            "VALUES (:t, :p, :d, :a, :v, :prob)"
        ),
        [
            {"t": tenant_id, "p": project_id, "d": as_of, "a": r.activity_id, "v": version,
             "prob": float(r.slip_probability)}
            for r in feats.itertuples()
        ],
    )  # fmt: skip

    snap = snapshot_on_or_before(conn, project_id, as_of)
    names = pd.DataFrame(
        conn.execute(
            text("SELECT activity_id, name FROM activity WHERE snapshot_id = :s"),
            {"s": snap["snapshot_id"]},
        ).all(),
        columns=["activity_id", "name"],
    )
    out = feats.merge(names, on="activity_id", how="left")
    out["as_of_date"], out["model_version"] = as_of, version
    return out.sort_values("slip_probability", ascending=False).reset_index(drop=True)