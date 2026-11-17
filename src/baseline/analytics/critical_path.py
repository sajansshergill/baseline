"""The driving critical path: the chain of activities that is setting the finish date.

Start from the activity that finishes last and walk backwards. At each step, follow the
predecessor whose logic actually pushed this activity's start (the "driving" link).
"""

from __future__ import annotations

from collections import defaultdict

from baseline.analytics.cpm import CpmResult
from baseline.canonical.models import Relationship, RelType, Schedule


def _link_target(rel: Relationship, cpm: CpmResult, succ_duration: int) -> int:
    p = cpm.activities[rel.predecessor_id]
    lag = round(rel.lag_days)
    return {
        RelType.FS: p.ef + lag,
        RelType.SS: p.es + lag,
        RelType.FF: p.ef + lag - succ_duration,
        RelType.SF: p.es + lag - succ_duration,
    }[rel.rel_type]


def driving_path(schedule: Schedule, cpm: CpmResult) -> list[str]:
    """Activity IDs on the driving path, first to last."""
    incomplete = [i for i, r in cpm.activities.items() if r.total_float is not None]
    if not incomplete:
        return []
    preds: dict[str, list[Relationship]] = defaultdict(list)
    for r in schedule.relationships:
        preds[r.successor_id].append(r)

    current = max(incomplete, key=lambda i: (cpm.activities[i].ef, i))
    path = [current]
    seen = {current}
    while True:
        res = cpm.activities[current]
        duration = res.ef - res.es
        driver = next(
            (
                r.predecessor_id
                for r in sorted(preds[current], key=lambda r: r.predecessor_id)
                if r.predecessor_id not in seen
                and cpm.activities[r.predecessor_id].total_float is not None
                and _link_target(r, cpm, duration) == res.es
            ),
            None,
        )
        if driver is None:
            break
        path.append(driver)
        seen.add(driver)
        current = driver
    return list(reversed(path))


def critical_activities(cpm: CpmResult) -> set[str]:
    return {i for i, r in cpm.activities.items() if r.is_critical}