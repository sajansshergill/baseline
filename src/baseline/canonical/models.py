"""The one common schedule format inside Baseline.

Every parser (P6, MS Project, Excel) converts its file into these models.
Everything downstream (database, analytics, ML) only ever sees these.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class ActivityType(StrEnum):
    TASK = "task"
    START_MILESTONE = "start_milestone"
    FINISH_MILESTONE = "finish_milestone"


class ActivityStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class RelType(StrEnum):
    FS = "FS"  # finish-to-start (the normal one)
    SS = "SS"  # start-to-start
    FF = "FF"  # finish-to-finish
    SF = "SF"  # start-to-finish (rare)


class ConstraintType(StrEnum):
    START_ON = "start_on"
    FINISH_ON = "finish_on"
    START_NO_EARLIER = "start_no_earlier"
    START_NO_LATER = "start_no_later"
    FINISH_NO_EARLIER = "finish_no_earlier"
    FINISH_NO_LATER = "finish_no_later"
    MANDATORY_START = "mandatory_start"
    MANDATORY_FINISH = "mandatory_finish"


# DCMA treats these as "hard" constraints because they override logic.
HARD_CONSTRAINTS = {
    ConstraintType.START_ON,
    ConstraintType.FINISH_ON,
    ConstraintType.START_NO_LATER,
    ConstraintType.FINISH_NO_LATER,
    ConstraintType.MANDATORY_START,
    ConstraintType.MANDATORY_FINISH,
}


class Calendar(BaseModel):
    calendar_id: str
    name: str = "Standard"
    hours_per_day: float = 8.0
    # Python weekday numbers: Monday = 0 ... Sunday = 6
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    holidays: list[date] = Field(default_factory=list)


class Activity(BaseModel):
    activity_id: str
    name: str
    wbs: str | None = None
    activity_type: ActivityType = ActivityType.TASK
    status: ActivityStatus = ActivityStatus.NOT_STARTED
    percent_complete: float = 0.0
    original_duration_days: float = 0.0
    remaining_duration_days: float = 0.0

    # Baseline (target) dates: what the plan originally promised.
    planned_start: date | None = None
    planned_finish: date | None = None

    # What really happened.
    actual_start: date | None = None
    actual_finish: date | None = None

    # Dates as calculated by the source tool (we recalculate our own too).
    early_start: date | None = None
    early_finish: date | None = None
    late_start: date | None = None
    late_finish: date | None = None
    total_float_days: float | None = None

    constraint_type: ConstraintType | None = None
    constraint_date: date | None = None
    calendar_id: str | None = None
    delay_note: str | None = None

    @property
    def is_milestone(self) -> bool:
        return self.activity_type != ActivityType.TASK


class Relationship(BaseModel):
    predecessor_id: str
    successor_id: str
    rel_type: RelType = RelType.FS
    lag_days: float = 0.0


class Schedule(BaseModel):
    """One full schedule as of one data date."""

    project_id: str
    project_name: str
    data_date: date | None
    source_format: str
    activities: list[Activity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    calendars: list[Calendar] = Field(default_factory=list)


class ProgressRow(BaseModel):
    activity_id: str | None
    description: str | None = None
    reported_pct: float | None = None
    report_date: date | None = None
    remark: str | None = None
    source_row: int  # spreadsheet row number, so problems can be traced back


class ProgressReport(BaseModel):
    """A trade contractor's progress sheet. Not a full schedule, just % complete claims."""

    project_id: str
    trade: str
    source_format: str = "progress_xlsx"
    rows: list[ProgressRow] = Field(default_factory=list)


ParsedFile = Schedule | ProgressReport