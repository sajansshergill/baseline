"""Contract rules that decide load vs quarantine."""

from datetime import date

from baseline.canonical.contracts import check_data_date_order, find_cycle, validate_schedule
from baseline.canonical.models import Activity, Relationship, RelType, Schedule


def _schedule(activities, relationships=None):
    return Schedule(
        project_id="P1",
        project_name="Test",
        data_date=date(2026, 1, 5),
        source_format="xer",
        activities=activities,
        relationships=relationships or [],
    )


def test_empty_schedule_is_rejected():
    result = validate_schedule(_schedule([]))
    assert not result.ok
    assert result.first.reason_code == "EMPTY_SCHEDULE"


def test_duplicate_activity_ids():
    acts = [
        Activity(activity_id="A1", name="One"),
        Activity(activity_id="A1", name="Also one"),
    ]
    result = validate_schedule(_schedule(acts))
    assert any(v.reason_code == "DUPLICATE_ACTIVITY_ID" for v in result.violations)


def test_circular_logic():
    acts = [
        Activity(activity_id="A", name="A"),
        Activity(activity_id="B", name="B"),
    ]
    rels = [
        Relationship(predecessor_id="A", successor_id="B", rel_type=RelType.FS),
        Relationship(predecessor_id="B", successor_id="A", rel_type=RelType.FS),
    ]
    result = validate_schedule(_schedule(acts, rels))
    assert any(v.reason_code == "CIRCULAR_LOGIC" for v in result.violations)
    assert find_cycle({"A", "B"}, [("A", "B"), ("B", "A")]) == ["A", "B"]


def test_data_date_regression():
    v = check_data_date_order(date(2026, 1, 1), date(2026, 2, 1))
    assert v is not None
    assert v.reason_code == "DATA_DATE_REGRESSION"
    assert check_data_date_order(date(2026, 2, 1), date(2026, 2, 1)) is None
