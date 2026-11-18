"""Critical-path math on a tiny schedule."""

from datetime import date

from baseline.analytics.cpm import compute_cpm
from baseline.canonical.models import (
    Activity,
    ActivityStatus,
    Calendar,
    Relationship,
    RelType,
    Schedule,
)


def test_simple_finish_to_start_chain():
    cal = Calendar(calendar_id="C1", work_days=[0, 1, 2, 3, 4])
    acts = [
        Activity(
            activity_id="A",
            name="A",
            original_duration_days=5,
            remaining_duration_days=5,
            status=ActivityStatus.NOT_STARTED,
            calendar_id="C1",
        ),
        Activity(
            activity_id="B",
            name="B",
            original_duration_days=5,
            remaining_duration_days=5,
            status=ActivityStatus.NOT_STARTED,
            calendar_id="C1",
        ),
    ]
    rels = [Relationship(predecessor_id="A", successor_id="B", rel_type=RelType.FS)]
    schedule = Schedule(
        project_id="P",
        project_name="P",
        data_date=date(2026, 1, 5),
        source_format="xer",
        activities=acts,
        relationships=rels,
        calendars=[cal],
    )
    cpm = compute_cpm(schedule)
    assert cpm.activities["A"].total_float == 0
    assert cpm.activities["B"].total_float == 0
    assert cpm.project_finish is not None
