"""Compare two schedule snapshots."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.analytics.diff import diff_schedules
from _shared import schedule_on, sidebar_context, snapshots_for

st.set_page_config(page_title="Snapshot diff", layout="wide")
tenant, project = sidebar_context()
st.title("Snapshot diff")

if not project:
    st.stop()

snaps = snapshots_for(tenant, project)
if len(snaps) < 2:
    st.warning("Need at least two snapshots to compare.")
    st.stop()

dates = [s["data_date"] for s in snaps]
left, right = st.columns(2)
from_date = left.selectbox("From", dates, index=0)
to_date = right.selectbox("To", dates, index=len(dates) - 1)

if from_date >= to_date:
    st.error("`From` must be earlier than `To`.")
    st.stop()

_, old = schedule_on(tenant, project, from_date)
_, new = schedule_on(tenant, project, to_date)
if not old or not new:
    st.error("Could not load one of the schedules.")
    st.stop()

diff = diff_schedules(old, new)
st.metric("Finish slip (calendar days)", f"{diff.finish_slip_days:+d}")
st.code("\n".join(diff.summary_lines()), language=None)

tabs = st.tabs(["Milestones", "Float erosion", "Duration", "Critical path"])
with tabs[0]:
    st.dataframe(
        [{"id": m.activity_id, "name": m.name, "before": str(m.before), "after": str(m.after)}
         for m in diff.milestone_moves],
        use_container_width=True,
        hide_index=True,
    )
with tabs[1]:
    st.dataframe(
        [{"id": c.activity_id, "name": c.name, "before": c.before, "after": c.after}
         for c in diff.float_erosion],
        use_container_width=True,
        hide_index=True,
    )
with tabs[2]:
    st.dataframe(
        [{"id": c.activity_id, "name": c.name, "before": c.before, "after": c.after}
         for c in diff.duration_changes],
        use_container_width=True,
        hide_index=True,
    )
with tabs[3]:
    c1, c2 = st.columns(2)
    c1.write("Entered critical")
    c1.write(diff.entered_critical or "—")
    c2.write("Left critical")
    c2.write(diff.left_critical or "—")
