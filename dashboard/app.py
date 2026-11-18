"""Baseline home — schedule intelligence for data-center construction."""

from __future__ import annotations

import streamlit as st

from _shared import sidebar_context, snapshots_for

st.set_page_config(page_title="Baseline", page_icon="◈", layout="wide")

tenant, project = sidebar_context()

st.title("Baseline")
st.caption("Know when a data-center project is slipping, before the deadline moves.")

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("#### Project health")
    st.write("DCMA 14-point scores across every weekly update.")
with col2:
    st.markdown("#### Snapshot diff")
    st.write("What changed between any two data dates.")
with col3:
    st.markdown("#### Slip risk")
    st.write("Activities most likely to slip next.")

st.markdown("---")
st.markdown(
    "Open **3D Campus** (`viz/`) for an animated construction timeline of the hyperscale campus."
)

if project:
    snaps = snapshots_for(tenant, project)
    st.subheader(f"{project}")
    st.metric("Snapshots loaded", len(snaps))
    if snaps:
        st.dataframe(
            [
                {
                    "data_date": str(s["data_date"]),
                    "ingested_at": str(s["ingested_at"]),
                    "snapshot_id": str(s["snapshot_id"])[:8],
                }
                for s in snaps
            ],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.info(
        "Demo schedules load automatically on Streamlit Cloud. "
        "For a live DB: `make up && make migrate && make seed && make load`."
    )
