"""Shared Streamlit helpers: tenant/project pickers, DB access, and cloud demo mode.

On Streamlit Community Cloud there is usually no Postgres. When the database is
unreachable we fall back to parsing sample_data/*.xer so health + diff still work.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import streamlit as st

from baseline.canonical.models import Schedule
from baseline.db.reader import list_projects, read_schedule, snapshot_on_or_before
from baseline.db.session import tenant_connection
from baseline.db.writer import current_snapshots
from baseline.parsers.xer import XerParser

DEFAULT_TENANT = os.getenv("BASELINE_TENANT", "demo_owner")
TENANTS = [t.strip() for t in os.getenv("BASELINE_TENANTS", "demo_owner,demo_gc").split(",") if t]
SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "sample_data" / "hyperscale_campus"
DEMO_PROJECT = "HYPERSCALE_CAMPUS"


@st.cache_resource(show_spinner="Loading sample schedules…")
def _demo_schedules() -> list[Schedule]:
    parser = XerParser()
    out: list[Schedule] = []
    if not SAMPLE_ROOT.exists():
        return out
    for path in sorted(SAMPLE_ROOT.glob("week_*.xer")):
        name = path.name
        if any(tag in name for tag in ("partial", "resend", "rev1")):
            continue
        try:
            parsed = parser.parse(path.read_bytes(), name)
        except Exception:  # noqa: BLE001 — skip unreadable sample files
            continue
        if isinstance(parsed, Schedule) and parsed.data_date is not None:
            out.append(parsed)
    return out


def _db_ok(tenant: str) -> bool:
    try:
        with tenant_connection(tenant) as conn:
            list_projects(conn)
        return True
    except Exception:  # noqa: BLE001
        return False


def using_demo() -> bool:
    return bool(st.session_state.get("baseline_demo"))


def sidebar_context() -> tuple[str, str | None]:
    st.sidebar.title("Baseline")
    tenant = st.sidebar.selectbox(
        "Tenant",
        TENANTS,
        index=TENANTS.index(DEFAULT_TENANT) if DEFAULT_TENANT in TENANTS else 0,
    )

    if _db_ok(tenant):
        st.session_state["baseline_demo"] = False
        with tenant_connection(tenant) as conn:
            projects = list_projects(conn)
        if not projects:
            st.sidebar.warning("No projects loaded yet. Run `make seed && make load`.")
            return tenant, None
        project = st.sidebar.selectbox("Project", projects)
        return tenant, project

    schedules = _demo_schedules()
    if not schedules:
        st.sidebar.error("Database unavailable and no sample schedules found.")
        st.session_state["baseline_demo"] = False
        return tenant, None

    st.session_state["baseline_demo"] = True
    st.sidebar.info("Demo mode — sample schedules (no Postgres).")
    project = st.sidebar.selectbox("Project", [DEMO_PROJECT])
    return tenant, project


def snapshots_for(tenant: str, project: str) -> list[dict]:
    if using_demo():
        return [
            {
                "snapshot_id": uuid.uuid5(uuid.NAMESPACE_URL, f"demo:{i}:{s.data_date}"),
                "data_date": s.data_date,
                "ingested_at": s.data_date,
                "upload_id": None,
                "_demo_index": i,
            }
            for i, s in enumerate(_demo_schedules())
        ]
    with tenant_connection(tenant) as conn:
        return current_snapshots(conn, project)


def schedule_on(tenant: str, project: str, on: date):
    if using_demo():
        schedules = [s for s in _demo_schedules() if s.data_date <= on]
        if not schedules:
            return None, None
        schedule = schedules[-1]
        snap = {
            "snapshot_id": uuid.uuid5(uuid.NAMESPACE_URL, f"demo:{schedule.data_date}"),
            "data_date": schedule.data_date,
        }
        return snap, schedule
    with tenant_connection(tenant) as conn:
        snap = snapshot_on_or_before(conn, project, on)
        if not snap:
            return None, None
        return snap, read_schedule(conn, snap["snapshot_id"])


def schedule_for_snapshot(tenant: str, snap: dict) -> Schedule:
    if using_demo():
        return _demo_schedules()[snap["_demo_index"]]
    with tenant_connection(tenant) as conn:
        return read_schedule(conn, snap["snapshot_id"])
