"""Rejected uploads and why they were blocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline.db.reader import list_quarantine
from baseline.db.session import tenant_connection
from _shared import sidebar_context, using_demo

st.set_page_config(page_title="Quarantine", layout="wide")
tenant, _project = sidebar_context()
st.title("Quarantine")
st.caption("Files that failed a contract rule and never entered the schedule tables.")

if using_demo():
    st.info(
        "Quarantine history lives in Postgres. "
        "In demo mode, rejected sample files include `week_41_partial.xer` "
        "(truncated export) after a real `baseline load`."
    )
    st.stop()

try:
    with tenant_connection(tenant) as conn:
        rows = list_quarantine(conn)
except Exception as exc:  # noqa: BLE001
    st.error(f"Database unavailable: {exc}")
    st.stop()

if not rows:
    st.success("No quarantined uploads — inbox is clean.")
    st.stop()

st.metric("Quarantine events", len(rows))
st.dataframe(
    [
        {
            "when": str(r["created_at"]),
            "file": r["filename"],
            "project": r["project_id"],
            "reason": r["reason_code"],
            "rule": r["rule"],
            "detail": r["detail"],
        }
        for r in rows
    ],
    use_container_width=True,
    hide_index=True,
)

picked = st.selectbox("Inspect sample rows", [r["filename"] for r in rows])
match = next(r for r in rows if r["filename"] == picked)
st.json(
    match["sample"]
    if isinstance(match["sample"], (dict, list))
    else json.loads(match["sample"] or "[]")
)
