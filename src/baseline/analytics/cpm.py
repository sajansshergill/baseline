"""Critical Path Method (CPM): when can each activity happen, and how much slack does it have?

Forward pass  -> early start / early finish (as soon as logic allows)
Backward pass -> late start / late finish (as late as possible without delaying the end)
Total float   =  late finish - early finish   (in working days)
Critical      =  total float <= 0

Conventions, all in working-day numbers from WorkCalendar:
- An activity occupies days [es, ef). So ef = es + duration, and a milestone has ef = es.
- Work left to do can't start before the data date.
- Complete activities are fixed at their actual dates and have no float.

Every source format (P6, MS Project) gets scored with this same code, so health checks and
ML features mean the same thing regardless of where the schedule came from.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Connection, delete

from baseline.analytics.calendar import WorkCalendar
from baseline.canonical.models import (
    Activity,
    ActivityStatus,
    ActivityType,
    ConstraintType,
    Relationship,
    RelType,
    Schedule,
)
from baseline.db.tables import table


@dataclass
class ActivityResult:
    es: int
    ef: int
    ls: int
    lf: int
    total_float: float | None  # working days; None for complete activities
    is_critical: bool
    early_finish_day: int  # working-day number of the calendar day the activity finishes
    late_finish_day: int


@dataclass
class CpmResult:
    calendar: WorkCalendar
    data_date_index: int
    activities: dict[str, ActivityResult]
    project_finish_index: int

    def finish_date(self, activity_id: str) -> date:
        return self.calendar.date(self.activities[activity_id].early_finish_day)

    def start_date(self, activity_id: str) -> date:
        return self.calendar.date(self.activities[activity_id].es)

    @property
    def project_finish(self) -> date:
        return self.calendar.date(max(self.project_finish_index - 1, 0))


def _finish_day(a: Activity, start: int, end: int, floor: int) -> int:
    """Which calendar day does something occupying [start, end) finish on?

    Tasks finish on the last day they work (end - 1). A finish milestone happens at the end
    of its predecessor's last day, which is also end - 1. A start milestone sits on start.
    """
    if a.activity_type == ActivityType.START_MILESTONE:
        return start
    if a.activity_type == ActivityType.FINISH_MILESTONE:
        return end - 1 if a.status == ActivityStatus.COMPLETE else max(end - 1, floor)
    return max(end - 1, start)


def _duration(a: Activity) -> int:
    if a.status == ActivityStatus.COMPLETE or a.is_milestone:
        return 0
    return max(0, math.ceil(a.remaining_duration_days - 1e-9))


def topological_order(activities: list[Activity], rels: list[Relationship]) -> list[str]:
    ids = [a.activity_id for a in activities]
    indegree = {i: 0 for i in ids}
    succ: dict[str, list[str]] = defaultdict(list)
    for r in rels:
        succ[r.predecessor_id].append(r.successor_id)
        indegree[r.successor_id] += 1
    queue = deque(i for i in ids if indegree[i] == 0)
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(n)
        for s in succ[n]:
            indegree[s] -= 1
            if indegree[s] == 0:
                queue.append(s)
    if len(order) != len(ids):
        raise ValueError("schedule has a dependency loop; contracts should have caught it")
    return order


def compute_cpm(schedule: Schedule) -> CpmResult:
    assert schedule.data_date is not None
    cal = WorkCalendar(schedule.data_date, schedule.calendars[0] if schedule.calendars else None)
    dd = cal.index(schedule.data_date)
    acts = {a.activity_id: a for a in schedule.activities}
    dur = {i: _duration(a) for i, a in acts.items()}
    preds: dict[str, list[Relationship]] = defaultdict(list)
    succs: dict[str, list[Relationship]] = defaultdict(list)
    for r in schedule.relationships:
        preds[r.successor_id].append(r)
        succs[r.predecessor_id].append(r)
    order = topological_order(schedule.activities, schedule.relationships)

    # ---- forward pass -------------------------------------------------------------------
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    for i in order:
        a, d = acts[i], dur[i]
        if a.status == ActivityStatus.COMPLETE:
            start = cal.index(a.actual_start or a.actual_finish or schedule.data_date)
            if a.activity_type == ActivityType.FINISH_MILESTONE and a.actual_finish:
                start = finish = cal.index(a.actual_finish) + 1
            elif a.actual_finish:
                finish = cal.index(a.actual_finish) + (0 if a.is_milestone else 1)
            else:
                finish = start
            es[i], ef[i] = start, max(finish, start)
            continue
        if a.status == ActivityStatus.IN_PROGRESS:
            es[i] = cal.index(a.actual_start) if a.actual_start else dd
            ef[i] = max(dd, es[i]) + d
            continue

        start = dd
        for r in preds[i]:
            lag = round(r.lag_days)
            p = r.predecessor_id
            start = max(
                start,
                {
                    RelType.FS: ef[p] + lag,
                    RelType.SS: es[p] + lag,
                    RelType.FF: ef[p] + lag - d,
                    RelType.SF: es[p] + lag - d,
                }[r.rel_type],
            )
        if a.constraint_date and a.constraint_type in (
            ConstraintType.START_NO_EARLIER,
            ConstraintType.START_ON,
            ConstraintType.MANDATORY_START,
        ):
            start = max(start, cal.index(a.constraint_date))
        if a.constraint_date and a.constraint_type == ConstraintType.FINISH_NO_EARLIER:
            start = max(start, cal.index(a.constraint_date) + 1 - d)
        es[i], ef[i] = start, start + d

    finish = max(ef.values()) if ef else dd

    # ---- backward pass ------------------------------------------------------------------
    lf: dict[str, int] = {}
    ls: dict[str, int] = {}
    for i in reversed(order):
        a, d = acts[i], dur[i]
        late = finish
        for r in succs[i]:
            lag = round(r.lag_days)
            s = r.successor_id
            late = min(
                late,
                {
                    RelType.FS: ls[s] - lag,
                    RelType.SS: ls[s] - lag + d,
                    RelType.FF: lf[s] - lag,
                    RelType.SF: lf[s] - lag + d,
                }[r.rel_type],
            )
        if a.constraint_date and a.constraint_type in (
            ConstraintType.FINISH_NO_LATER,
            ConstraintType.FINISH_ON,
            ConstraintType.MANDATORY_FINISH,
        ):
            end_of_day = a.activity_type != ActivityType.START_MILESTONE
            late = min(late, cal.index(a.constraint_date) + (1 if end_of_day else 0))
        if a.constraint_date and a.constraint_type == ConstraintType.START_NO_LATER:
            late = min(late, cal.index(a.constraint_date) + d)
        lf[i], ls[i] = late, late - d

    results = {}
    for i, a in acts.items():
        efd = _finish_day(a, es[i], ef[i], dd)
        if a.status == ActivityStatus.COMPLETE:
            results[i] = ActivityResult(es[i], ef[i], es[i], ef[i], None, False, efd, efd)
        else:
            tf = float(lf[i] - ef[i])
            lfd = _finish_day(a, ls[i], lf[i], dd)
            results[i] = ActivityResult(es[i], ef[i], ls[i], lf[i], tf, tf <= 0, efd, lfd)
    return CpmResult(cal, dd, results, finish)


def store_cpm(
    conn: Connection, tenant_id: str, snapshot_id: uuid.UUID, schedule: Schedule
) -> CpmResult:
    """Compute CPM for a snapshot and save it to activity_cpm (replacing any old result)."""
    result = compute_cpm(schedule)
    t = table("activity_cpm")
    conn.execute(delete(t).where(t.c.snapshot_id == snapshot_id))
    rows = []
    for i, r in result.activities.items():
        rows.append(
            {
                "tenant_id": tenant_id,
                "snapshot_id": snapshot_id,
                "activity_id": i,
                "early_start": result.calendar.date(r.es),
                "early_finish": result.calendar.date(r.early_finish_day),
                "late_start": result.calendar.date(r.ls),
                "late_finish": result.calendar.date(r.late_finish_day),
                "total_float_days": r.total_float,
                "is_critical": r.is_critical,
            }
        )
    if rows:
        conn.execute(t.insert(), rows)
    return result