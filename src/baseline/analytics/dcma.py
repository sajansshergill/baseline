"""DCMA 14-point schedule assessment.

A standard checklist from the US Defense Contract Management Agency for judging whether a
schedule is well built. Most checks are "what share of incomplete tasks break this rule",
compared with a threshold (usually 5%).

Populations follow common practice:
- "tasks" = incomplete, non-milestone activities
- relationship checks look at links whose successor is incomplete
"""

from __future__ import annotations

from dataclasses import dataclass, field

from baseline.analytics.cpm import CpmResult, compute_cpm
from baseline.analytics.critical_path import driving_path
from baseline.canonical.models import HARD_CONSTRAINTS, ActivityStatus, RelType, Schedule

HIGH_DAYS = 44  # 2 working months
CP_TEST_DELAY = 600


@dataclass
class DcmaCheck:
    number: int
    name: str
    value: float | None
    threshold: str
    passed: bool | None  # None = not applicable to this data
    offenders: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def display_value(self) -> str:
        if self.value is None:
            return "n/a"
        if self.number in (13, 14):
            return f"{self.value:.2f}"
        if self.number == 12:
            return "pass" if self.passed else "fail"
        return f"{self.value:.1%}"


def _share(bad: list[str], total: int) -> float:
    return len(bad) / total if total else 0.0


def run_dcma(schedule: Schedule, cpm: CpmResult | None = None) -> list[DcmaCheck]:
    cpm = cpm or compute_cpm(schedule)
    dd = schedule.data_date
    acts = {a.activity_id: a for a in schedule.activities}
    tasks = [
        a for a in schedule.activities
        if a.status != ActivityStatus.COMPLETE and not a.is_milestone
    ]  # fmt: skip
    task_ids = {a.activity_id for a in tasks}
    n = len(tasks)
    has_pred = {r.successor_id for r in schedule.relationships}
    has_succ = {r.predecessor_id for r in schedule.relationships}
    live_links = [
        r for r in schedule.relationships if acts[r.successor_id].status != ActivityStatus.COMPLETE
    ]
    checks: list[DcmaCheck] = []

    # 1. Logic: every task needs a predecessor and a successor.
    bad = sorted(i for i in task_ids if i not in has_pred or i not in has_succ)
    checks.append(DcmaCheck(1, "Logic", _share(bad, n), "<= 5%", _share(bad, n) <= 0.05, bad))

    # 2. Leads (negative lag) should never be used.
    leads = [f"{r.predecessor_id}->{r.successor_id}" for r in live_links if r.lag_days < 0]
    checks.append(
        DcmaCheck(2, "Leads", _share(leads, len(live_links)), "0%", not leads, leads)
    )

    # 3. Lags (positive lag) hide work; keep them rare.
    lags = [f"{r.predecessor_id}->{r.successor_id}" for r in live_links if r.lag_days > 0]
    share = _share(lags, len(live_links))
    checks.append(DcmaCheck(3, "Lags", share, "<= 5%", share <= 0.05, lags))

    # 4. Relationship types: at least 90% finish-to-start.
    non_fs = [
        f"{r.predecessor_id}->{r.successor_id}" for r in live_links if r.rel_type != RelType.FS
    ]
    fs_share = 1 - _share(non_fs, len(live_links))
    checks.append(DcmaCheck(4, "Relationship types (FS)", fs_share, ">= 90%",
                            fs_share >= 0.90, non_fs))  # fmt: skip

    # 5. Hard constraints override logic.
    hard = sorted(a.activity_id for a in tasks if a.constraint_type in HARD_CONSTRAINTS)
    checks.append(
        DcmaCheck(5, "Hard constraints", _share(hard, n), "<= 5%", _share(hard, n) <= 0.05, hard)
    )

    # 6. High float: more than 44 days of float usually means missing logic.
    high_float = sorted(
        i for i in task_ids if (cpm.activities[i].total_float or 0) > HIGH_DAYS
    )
    share = _share(high_float, n)
    checks.append(DcmaCheck(6, "High float", share, "<= 5%", share <= 0.05, high_float))

    # 7. Negative float: the schedule already can't meet a constraint date.
    neg = sorted(i for i in task_ids if (cpm.activities[i].total_float or 0) < 0)
    checks.append(DcmaCheck(7, "Negative float", _share(neg, n), "0%", not neg, neg))

    # 8. High duration: long tasks are hard to track; break them up.
    long_ = sorted(a.activity_id for a in tasks if a.original_duration_days > HIGH_DAYS)
    share = _share(long_, n)
    checks.append(DcmaCheck(8, "High duration", share, "<= 5%", share <= 0.05, long_))

    # 9. Invalid dates: forecasts in the past, or actuals in the future.
    invalid = sorted(
        a.activity_id
        for a in schedule.activities
        if (a.actual_start and a.actual_start > dd)
        or (a.actual_finish and a.actual_finish > dd)
        or (a.status == ActivityStatus.NOT_STARTED and a.early_start and a.early_start < dd)
    )
    share = _share(invalid, len(schedule.activities))
    checks.append(DcmaCheck(9, "Invalid dates", share, "0%", not invalid, invalid))

    # 10. Resources: our sources don't carry resource loading, so this check doesn't apply.
    checks.append(DcmaCheck(10, "Resources", None, "n/a", None, [],
                            "resource assignments are not ingested"))  # fmt: skip

    # 11. Missed tasks: due by the data date per the baseline, but not finished on time.
    due = [
        a for a in schedule.activities
        if not a.is_milestone and a.planned_finish and a.planned_finish <= dd
    ]  # fmt: skip
    missed = sorted(
        a.activity_id for a in due if a.actual_finish is None or a.actual_finish > a.planned_finish
    )
    share = _share(missed, len(due))
    checks.append(DcmaCheck(11, "Missed tasks", share, "<= 5%", share <= 0.05, missed))

    # 12. Critical path test: delay a critical task by 600 days; the finish must move too.
    path = [i for i in driving_path(schedule, cpm) if i in task_ids]
    if path:
        probe = path[0]
        delayed = schedule.model_copy(deep=True)
        for a in delayed.activities:
            if a.activity_id == probe:
                a.remaining_duration_days += CP_TEST_DELAY
        moved = compute_cpm(delayed).project_finish_index - cpm.project_finish_index
        ok = moved >= CP_TEST_DELAY
        checks.append(DcmaCheck(12, "Critical path test", float(moved), "finish moves 600d", ok,
                                [] if ok else [probe], f"delayed {probe}"))  # fmt: skip
    else:
        checks.append(DcmaCheck(12, "Critical path test", None, "finish moves 600d", None))

    # 13. CPLI: can the remaining critical path still make the contract finish date?
    end_id = max(cpm.activities, key=lambda i: (cpm.activities[i].ef, i))
    end = acts[end_id]
    target = end.constraint_date or end.planned_finish
    cp_length = cpm.project_finish_index - cpm.data_date_index
    if target and cp_length > 0:
        # Both sides measured as "end of the finish day", so on-time = 0 float.
        end_float = cpm.calendar.index(target) + 1 - cpm.project_finish_index
        cpli = (cp_length + end_float) / cp_length
        checks.append(DcmaCheck(13, "CPLI", cpli, ">= 0.95", cpli >= 0.95, [],
                                f"target {target}, forecast {cpm.project_finish}"))  # fmt: skip
    else:
        checks.append(DcmaCheck(13, "CPLI", None, ">= 0.95", None, [], "no target finish date"))

    # 14. BEI: tasks actually finished vs tasks the baseline said should be finished.
    if due:
        done = sum(1 for a in schedule.activities if not a.is_milestone and a.actual_finish)
        bei = done / len(due)
        checks.append(DcmaCheck(14, "BEI", bei, ">= 0.95", bei >= 0.95))
    else:
        checks.append(DcmaCheck(14, "BEI", None, ">= 0.95", None, [], "nothing due yet"))

    return checks


def health_score(checks: list[DcmaCheck]) -> float:
    """Share of applicable checks that pass, 0-100."""
    applicable = [c for c in checks if c.passed is not None]
    return 100 * sum(c.passed for c in applicable) / len(applicable) if applicable else 0.0