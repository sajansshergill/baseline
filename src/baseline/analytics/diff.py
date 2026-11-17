"""Compare two snapshots of the same project: what changed between week A and week B?"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from baseline.analytics.cpm import CpmResult, compute_cpm
from baseline.canonical.models import Schedule


@dataclass
class Change:
    activity_id: str
    name: str
    before: object
    after: object


@dataclass
class SnapshotDiff:
    from_date: date
    to_date: date
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    duration_changes: list[Change] = field(default_factory=list)
    logic_added: list[tuple[str, str, str]] = field(default_factory=list)
    logic_removed: list[tuple[str, str, str]] = field(default_factory=list)
    float_erosion: list[Change] = field(default_factory=list)
    entered_critical: list[str] = field(default_factory=list)
    left_critical: list[str] = field(default_factory=list)
    milestone_moves: list[Change] = field(default_factory=list)
    finish_before: date | None = None
    finish_after: date | None = None

    @property
    def finish_slip_days(self) -> int:
        if self.finish_before and self.finish_after:
            return (self.finish_after - self.finish_before).days
        return 0

    def summary_lines(self) -> list[str]:
        lines = [
            f"Comparing {self.from_date} -> {self.to_date}",
            f"Forecast finish: {self.finish_before} -> {self.finish_after} "
            f"({self.finish_slip_days:+d} calendar days)",
            f"Activities added: {len(self.added)}, removed: {len(self.removed)}",
            f"Duration changes: {len(self.duration_changes)}",
            f"Logic links added: {len(self.logic_added)}, removed: {len(self.logic_removed)}",
            f"Activities that lost float: {len(self.float_erosion)}",
            f"Entered critical path: {len(self.entered_critical)}, "
            f"left: {len(self.left_critical)}",
        ]
        for m in self.milestone_moves:
            lines.append(f"  Milestone {m.activity_id} {m.name}: {m.before} -> {m.after}")
        for c in sorted(self.float_erosion, key=lambda c: c.after - c.before)[:10]:
            lines.append(f"  Float {c.activity_id} {c.name}: {c.before:g} -> {c.after:g} days")
        return lines


def _links(s: Schedule) -> set[tuple[str, str, str]]:
    return {(r.predecessor_id, r.successor_id, str(r.rel_type)) for r in s.relationships}


def diff_schedules(
    old: Schedule,
    new: Schedule,
    old_cpm: CpmResult | None = None,
    new_cpm: CpmResult | None = None,
    float_threshold_days: float = 5.0,
) -> SnapshotDiff:
    old_cpm = old_cpm or compute_cpm(old)
    new_cpm = new_cpm or compute_cpm(new)
    a_old = {a.activity_id: a for a in old.activities}
    a_new = {a.activity_id: a for a in new.activities}
    d = SnapshotDiff(from_date=old.data_date, to_date=new.data_date)

    d.added = sorted(a_new.keys() - a_old.keys())
    d.removed = sorted(a_old.keys() - a_new.keys())
    d.logic_added = sorted(_links(new) - _links(old))
    d.logic_removed = sorted(_links(old) - _links(new))

    for i in sorted(a_old.keys() & a_new.keys()):
        before, after = a_old[i], a_new[i]
        if before.original_duration_days != after.original_duration_days:
            d.duration_changes.append(
                Change(i, after.name, before.original_duration_days, after.original_duration_days)
            )
        f0 = old_cpm.activities[i].total_float
        f1 = new_cpm.activities[i].total_float
        if f0 is not None and f1 is not None and f0 - f1 >= float_threshold_days:
            d.float_erosion.append(Change(i, after.name, f0, f1))

        crit0, crit1 = old_cpm.activities[i].is_critical, new_cpm.activities[i].is_critical
        if crit1 and not crit0:
            d.entered_critical.append(i)
        if crit0 and not crit1:
            d.left_critical.append(i)

        if after.is_milestone:
            m0, m1 = old_cpm.finish_date(i), new_cpm.finish_date(i)
            if m0 != m1:
                d.milestone_moves.append(Change(i, after.name, m0, m1))

    d.finish_before, d.finish_after = old_cpm.project_finish, new_cpm.project_finish
    return d