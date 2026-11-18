"""Train the slip model for one tenant.

One model per tenant: a customer's schedule history never trains a model that another
customer's predictions come from.

The split is by time, never random. We train on earlier updates and test on later ones,
because that's how the model is used: it only ever predicts the future from the past.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import Connection

from baseline.config import get_settings
from baseline.db.reader import list_projects
from baseline.db.writer import current_snapshots
from baseline.ml.features import FEATURE_COLUMNS, compute_features
from baseline.ml.labels import compute_labels

PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "feature_fraction": 0.9,
    "is_unbalance": True,
    "seed": 42,
    "deterministic": True,
    "num_threads": 1,
    "verbose": -1,
}
NUM_ROUNDS = 200
TRAIN_SHARE = 0.7


@dataclass
class TrainReport:
    tenant_id: str
    model_version: str
    rows_train: int
    rows_test: int
    positive_rate: float
    auc: float | None
    precision: float | None
    recall: float | None
    trained_through: str


def build_dataset(
    conn: Connection, project_ids: list[str] | None = None, horizon_weeks: int | None = None
) -> pd.DataFrame:
    settings = get_settings()
    horizon = horizon_weeks or settings.slip_horizon_weeks
    frames = []
    for project_id in project_ids or list_projects(conn):
        for snap in current_snapshots(conn, project_id):
            as_of = snap["data_date"]
            labels = compute_labels(conn, project_id, as_of, horizon, settings.slip_tolerance_days)
            if labels is None or labels.empty:
                continue
            feats = compute_features(conn, project_id, as_of)
            df = feats.merge(labels, on="activity_id")
            df["project_id"], df["as_of_date"] = project_id, as_of
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def auc_score(y: np.ndarray, p: np.ndarray) -> float | None:
    """Area under the ROC curve via ranks (no scikit-learn needed)."""
    pos, neg = y == 1, y == 0
    if not pos.any() or not neg.any():
        return None
    ranks = pd.Series(p).rank().to_numpy()
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


def model_dir(tenant_id: str) -> Path:
    path = get_settings().model_dir / tenant_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def train(conn: Connection, tenant_id: str, project_ids: list[str] | None = None) -> TrainReport:
    data = build_dataset(conn, project_ids)
    if data.empty or data["slipped"].nunique() < 2:
        raise ValueError("not enough labelled history to train (need slips and non-slips)")

    dates = sorted(data["as_of_date"].unique())
    cutoff = dates[max(1, int(len(dates) * TRAIN_SHARE)) - 1]
    train_df = data[data["as_of_date"] <= cutoff]
    test_df = data[data["as_of_date"] > cutoff]

    booster = lgb.train(
        PARAMS,
        lgb.Dataset(train_df[FEATURE_COLUMNS], label=train_df["slipped"]),
        num_boost_round=NUM_ROUNDS,
    )

    auc = precision = recall = None
    if not test_df.empty:
        p = booster.predict(test_df[FEATURE_COLUMNS])
        y = test_df["slipped"].to_numpy()
        pred = p >= 0.5
        auc = auc_score(y, p)
        precision = float((pred & (y == 1)).sum() / pred.sum()) if pred.sum() else None
        recall = float((pred & (y == 1)).sum() / (y == 1).sum()) if (y == 1).sum() else None

    model_text = booster.model_to_string()
    version = "v" + hashlib.sha256(model_text.encode()).hexdigest()[:10]
    out = model_dir(tenant_id)
    (out / "slip_model.txt").write_text(model_text)
    report = TrainReport(
        tenant_id=tenant_id,
        model_version=version,
        rows_train=len(train_df),
        rows_test=len(test_df),
        positive_rate=round(float(data["slipped"].mean()), 4),
        auc=None if auc is None else round(auc, 4),
        precision=None if precision is None else round(precision, 4),
        recall=None if recall is None else round(recall, 4),
        trained_through=str(cutoff),
    )
    meta = {**asdict(report), "features": FEATURE_COLUMNS,
            "trained_at": datetime.now(UTC).isoformat()}  # fmt: skip
    (out / "slip_model.json").write_text(json.dumps(meta, indent=2))
    return report


def load_model(tenant_id: str) -> tuple[lgb.Booster, dict]:
    path = get_settings().model_dir / tenant_id
    if not (path / "slip_model.txt").exists():
        raise FileNotFoundError(f"no trained model for tenant {tenant_id}; run `baseline train`")
    booster = lgb.Booster(model_file=str(path / "slip_model.txt"))
    meta = json.loads((path / "slip_model.json").read_text())
    return booster, meta


def latest_data_date(conn: Connection, project_id: str) -> date | None:
    snaps = current_snapshots(conn, project_id)
    return snaps[-1]["data_date"] if snaps else None