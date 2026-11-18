"""DCMA health scores over time."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.analytics.dcma import health_score, run_dcma
from _shared import schedule_for_snapshot, sidebar_context, snapshots_for

st.set_page_config(page_title="Project health", layout="wide")
tenant, project = sidebar_context()
st.title("Project health")

if not project:
    st.stop()

snaps = snapshots_for(tenant, project)
if not snaps:
    st.warning("No snapshots yet.")
    st.stop()

rows = []
latest_checks = None
for s in snaps:
    schedule = schedule_for_snapshot(tenant, s)
    checks = run_dcma(schedule)
    latest_checks = checks
    rows.append(
        {
            "data_date": s["data_date"],
            "health_score": health_score(checks),
            "failing": sum(1 for c in checks if c.passed is False),
        }
    )

df = pd.DataFrame(rows)
c1, c2, c3 = st.columns(3)
c1.metric("Latest health", f"{df['health_score'].iloc[-1]:.0f}")
c2.metric("Failing checks", int(df["failing"].iloc[-1]))
c3.metric("Snapshots", len(df))

st.line_chart(df.set_index("data_date")["health_score"], height=280)
st.subheader(f"DCMA detail — {df['data_date'].iloc[-1]}")
if latest_checks:
    st.dataframe(
        [
            {
                "#": c.number,
                "check": c.name,
                "value": c.display_value,
                "target": c.threshold,
                "result": {True: "PASS", False: "FAIL", None: "N/A"}[c.passed],
            }
            for c in latest_checks
        ],
        use_container_width=True,
        hide_index=True,
    )
