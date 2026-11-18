"""Top slip-risk activities from the trained model."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.db.session import tenant_connection
from baseline.ml.predict import predict
from _shared import sidebar_context, using_demo

st.set_page_config(page_title="Slip risk", layout="wide")
tenant, project = sidebar_context()
st.title("Slip risk")

if not project:
    st.stop()

if using_demo():
    st.info(
        "Slip predictions need a trained model in Postgres. "
        "Connect `BASELINE_DATABASE_URL` in Streamlit secrets, then run "
        "`baseline load`, `baseline backfill`, and `baseline train`."
    )
    st.stop()

top_n = st.slider("Show top N", 5, 50, 15)

try:
    with tenant_connection(tenant) as conn:
        df = predict(conn, tenant, project)
except Exception as exc:  # noqa: BLE001
    st.warning(
        f"Predictions unavailable ({exc}). Train a model with "
        f"`baseline train --tenant {tenant}` after loading and backfilling data."
    )
    st.stop()

if df is None or df.empty:
    st.info("No predictions yet. Run `baseline backfill` then `baseline train`.")
    st.stop()

view = df.sort_values("slip_probability", ascending=False).head(top_n)
st.metric("As of", str(view["as_of_date"].iloc[0]) if "as_of_date" in view.columns else "—")
st.bar_chart(view.set_index("name")["slip_probability"], height=320)
st.dataframe(view, use_container_width=True, hide_index=True)
