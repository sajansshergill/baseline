"""Data contracts: the rules a file must pass before it's allowed into the database.

Every rule has a reason code. When a file breaks a rule, it's quarantined with that code,
a short explanation, and a few sample rows so a person can see the problem quickly.

Warnings are things worth knowing about that don't block a load.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

from baseline.canonical.models import ProgressReport, Schedule

SAMPLE_SIZE = 5
MAX_MISSING_ID_SHARE = 0.10  # progress sheets: up to 10% of rows may lack an activity ID


@dataclass
class Violation:
    reason_code: str
    rule: str
    detail: str
    sample: list[dict] = field(default_factory=list)


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def first(self) -> Violation | None:
        return self.violations[0] if self.violations else None


def find_cycle(nodes: set[str], edges: list[tuple[str, str]]) -> list[str]:
    """Return the activities stuck in a loop (empty list if there is none).

    Uses Kahn's algorithm: repeatedly remove activities with no remaining predecessors.
    Anything left at the end is part of a cycle.
    """
    indegree = {n: 0 for n in nodes}
    successors: dict[str, list[str]] = defaultdict(list)
    for pred, succ in edges:
        if pred in nodes and succ in nodes:
            successors[pred].append(succ)
            indegree[succ] += 1

    queue = deque(n for n, d in indegree.items() if d == 0)
    removed = 0
    while queue:
        node = queue.popleft()
        removed += 1
        for nxt in successors[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if removed == len(nodes):
        return []
    return sorted(n for n, d in indegree.items() if d > 0)


def validate_schedule(schedule: Schedule) -> ValidationResult:
    result = ValidationResult()
    v = result.violations

    if schedule.data_date is None:
        v.append(Violation("MISSING_DATA_DATE", "data_date_present", "schedule has no data date"))

    if not schedule.activities:
        v.append(Violation("EMPTY_SCHEDULE", "has_activities", "schedule has no activities"))
        return result

    missing = [a for a in schedule.activities if not a.activity_id.strip()]
    if missing:
        v.append(
            Violation(
                "MISSING_ACTIVITY_ID",
                "activity_id_present",
                f"{len(missing)} activities have no ID",
                [{"name": a.name} for a in missing[:SAMPLE_SIZE]],
            )
        )

    counts = Counter(a.activity_id for a in schedule.activities if a.activity_id.strip())
    dupes = sorted(k for k, c in counts.items() if c > 1)
    if dupes:
        v.append(
            Violation(
                "DUPLICATE_ACTIVITY_ID",
                "activity_id_unique",
                f"{len(dupes)} activity IDs appear more than once",
                [{"activity_id": d, "count": counts[d]} for d in dupes[:SAMPLE_SIZE]],
            )
        )

    bad_pct = [a for a in schedule.activities if not 0 <= a.percent_complete <= 100]
    if bad_pct:
        v.append(
            Violation(
                "INVALID_PERCENT",
                "percent_between_0_and_100",
                f"{len(bad_pct)} activities have % complete outside 0-100",
                [
                    {"activity_id": a.activity_id, "percent_complete": a.percent_complete}
                    for a in bad_pct[:SAMPLE_SIZE]
                ],
            )
        )

    bad_dur = [
        a
        for a in schedule.activities
        if a.original_duration_days < 0 or a.remaining_duration_days < 0
    ]
    if bad_dur:
        v.append(
            Violation(
                "NEGATIVE_DURATION",
                "duration_not_negative",
                f"{len(bad_dur)} activities have a negative duration",
                [{"activity_id": a.activity_id} for a in bad_dur[:SAMPLE_SIZE]],
            )
        )

    ids = set(counts)
    dangling = [
        r
        for r in schedule.relationships
        if r.predecessor_id not in ids or r.successor_id not in ids
    ]
    if dangling:
        v.append(
            Violation(
                "DANGLING_RELATIONSHIP",
                "relationship_endpoints_exist",
                f"{len(dangling)} relationships point at activities that don't exist",
                [
                    {"predecessor_id": r.predecessor_id, "successor_id": r.successor_id}
                    for r in dangling[:SAMPLE_SIZE]
                ],
            )
        )

    rel_counts = Counter(
        (r.predecessor_id, r.successor_id, r.rel_type) for r in schedule.relationships
    )
    rel_dupes = [k for k, c in rel_counts.items() if c > 1]
    if rel_dupes:
        v.append(
            Violation(
                "DUPLICATE_RELATIONSHIP",
                "relationship_unique",
                f"{len(rel_dupes)} relationships appear more than once",
                [
                    {"predecessor_id": p, "successor_id": s, "rel_type": str(t)}
                    for p, s, t in rel_dupes[:SAMPLE_SIZE]
                ],
            )
        )

    edges = [(r.predecessor_id, r.successor_id) for r in schedule.relationships]
    cycle = find_cycle(ids, edges)
    if cycle:
        v.append(
            Violation(
                "CIRCULAR_LOGIC",
                "no_dependency_loops",
                f"{len(cycle)} activities are caught in a dependency loop",
                [{"activity_id": a} for a in cycle[:SAMPLE_SIZE]],
            )
        )

    no_finish = [
        a.activity_id
        for a in schedule.activities
        if a.status == "complete" and a.actual_finish is None
    ]
    if no_finish:
        result.warnings.append(f"{len(no_finish)} complete activities have no actual finish date")

    return result


def check_data_date_order(new_date: date, latest_loaded: date | None) -> Violation | None:
    """A new update can't be older than the newest one we already have.

    The same data date is fine: that's a re-issued or corrected update, and the bitemporal
    model keeps both. An older date usually means someone uploaded last month's file by
    mistake, so a person has to confirm it (`baseline load --allow-backdated`).
    """
    if latest_loaded is not None and new_date < latest_loaded:
        return Violation(
            "DATA_DATE_REGRESSION",
            "data_date_not_backwards",
            f"data date {new_date} is earlier than the latest loaded update ({latest_loaded})",
            [{"data_date": str(new_date), "latest_loaded": str(latest_loaded)}],
        )
    return None


def validate_progress(report: ProgressReport) -> ValidationResult:
    result = ValidationResult()
    v = result.violations

    if not report.rows:
        v.append(Violation("EMPTY_REPORT", "has_rows", "progress sheet has no data rows"))
        return result

    missing = [r for r in report.rows if not r.activity_id]
    if missing:
        share = len(missing) / len(report.rows)
        sample = [{"row": r.source_row, "description": r.description} for r in missing[:5]]
        if share > MAX_MISSING_ID_SHARE:
            v.append(
                Violation(
                    "MISSING_ACTIVITY_ID",
                    "activity_id_present",
                    f"{len(missing)} of {len(report.rows)} rows ({share:.0%}) have no activity "
                    f"ID; the limit is {MAX_MISSING_ID_SHARE:.0%}",
                    sample,
                )
            )
        else:
            result.warnings.append(
                f"skipped {len(missing)} rows without an activity ID (rows "
                + ", ".join(str(r.source_row) for r in missing)
                + ")"
            )

    kept = [r for r in report.rows if r.activity_id]
    bad_pct = [r for r in kept if r.reported_pct is None or not 0 <= r.reported_pct <= 100]
    if bad_pct:
        v.append(
            Violation(
                "INVALID_PERCENT",
                "percent_between_0_and_100",
                f"{len(bad_pct)} rows have a missing or out-of-range % complete",
                [{"row": r.source_row, "value": r.reported_pct} for r in bad_pct[:SAMPLE_SIZE]],
            )
        )

    no_date = [r for r in kept if r.report_date is None]
    if no_date:
        v.append(
            Violation(
                "MISSING_REPORT_DATE",
                "report_date_present",
                f"{len(no_date)} rows have no report date",
                [{"row": r.source_row} for r in no_date[:SAMPLE_SIZE]],
            )
        )

    return result