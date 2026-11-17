"""Microsoft Project XML reader (and a small writer for sample files).

MS Project quirks worth knowing:
- Durations look like "PT40H0M0S" (40 hours).
- TotalSlack and LinkLag are stored in *tenths of a minute*. 4800 = 1 working day of 8h.
- Task UID 0 is the project summary row, and Summary=1 rows are WBS headings. Both skipped.
- Baseline (planned) dates live in <Baseline><Number>0</Number>...</Baseline>.
"""

from __future__ import annotations

import re
from datetime import date
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml.ElementTree import fromstring  # safe against malicious XML

from baseline.canonical.models import (
    Activity,
    ActivityStatus,
    ActivityType,
    Calendar,
    Relationship,
    RelType,
    Schedule,
)
from baseline.canonical.normalize import MSP_CONSTRAINTS, clean_text, parse_date
from baseline.parsers.base import ParseError, Parser

NS = "http://schemas.microsoft.com/project"
_N = {"p": NS}
TENTHS_OF_MINUTE_PER_DAY = 8 * 60 * 10  # 4800

# MS Project link type numbers.
_REL = {0: RelType.FF, 1: RelType.FS, 2: RelType.SF, 3: RelType.SS}
_DURATION = re.compile(r"^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$")


def _text(el: Element, tag: str) -> str | None:
    child = el.find(f"p:{tag}", _N)
    return clean_text(child.text) if child is not None else None


def _duration_days(value: str | None, hours_per_day: float = 8.0) -> float:
    if not value:
        return 0.0
    match = _DURATION.match(value)
    if not match:
        raise ParseError("MALFORMED_FILE", f"bad duration {value!r}")
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return round((hours + minutes / 60 + seconds / 3600) / hours_per_day, 4)


def _date(value: str | None, field: str) -> date | None:
    try:
        return parse_date(value)
    except ValueError as exc:
        raise ParseError("MALFORMED_FILE", f"{field}: {exc}") from exc


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError as exc:
        raise ParseError("MALFORMED_FILE", f"expected an integer, got {value!r}") from exc


class MspXmlParser(Parser):
    format_name = "msp_xml"

    def parse(self, content: bytes, filename: str) -> Schedule:
        try:
            root = fromstring(content)
        except XmlParseError as exc:
            msg = str(exc)
            code = "TRUNCATED_FILE" if "no element found" in msg or "unclosed" in msg else (
                "MALFORMED_FILE"
            )
            raise ParseError(code, f"XML error: {msg}") from exc

        if root.tag != f"{{{NS}}}Project":
            raise ParseError("MALFORMED_FILE", "root element is not an MS Project <Project>")

        project_name = _text(root, "Name") or _text(root, "Title") or "UNKNOWN"
        data_date = _date(_text(root, "StatusDate"), "StatusDate")

        activities: list[Activity] = []
        relationships: list[Relationship] = []
        for task in root.findall("p:Tasks/p:Task", _N):
            uid = _text(task, "UID")
            if uid in (None, "0") or _text(task, "Summary") == "1":
                continue

            pct = float(_text(task, "PercentComplete") or 0)
            actual_start = _date(_text(task, "ActualStart"), "ActualStart")
            if pct >= 100:
                status = ActivityStatus.COMPLETE
            elif pct > 0 or actual_start:
                status = ActivityStatus.IN_PROGRESS
            else:
                status = ActivityStatus.NOT_STARTED

            planned_start = planned_finish = None
            for bl in task.findall("p:Baseline", _N):
                if _text(bl, "Number") == "0":
                    planned_start = _date(_text(bl, "Start"), "Baseline.Start")
                    planned_finish = _date(_text(bl, "Finish"), "Baseline.Finish")

            slack = _text(task, "TotalSlack")
            constraint = MSP_CONSTRAINTS.get(_int(_text(task, "ConstraintType")))
            activities.append(
                Activity(
                    activity_id=uid,
                    name=_text(task, "Name") or uid,
                    wbs=_text(task, "WBS"),
                    activity_type=ActivityType.FINISH_MILESTONE
                    if _text(task, "Milestone") == "1"
                    else ActivityType.TASK,
                    status=status,
                    percent_complete=pct,
                    original_duration_days=_duration_days(_text(task, "Duration")),
                    remaining_duration_days=_duration_days(_text(task, "RemainingDuration")),
                    planned_start=planned_start,
                    planned_finish=planned_finish,
                    actual_start=actual_start,
                    actual_finish=_date(_text(task, "ActualFinish"), "ActualFinish"),
                    early_start=_date(_text(task, "EarlyStart"), "EarlyStart"),
                    early_finish=_date(_text(task, "EarlyFinish"), "EarlyFinish"),
                    late_start=_date(_text(task, "LateStart"), "LateStart"),
                    late_finish=_date(_text(task, "LateFinish"), "LateFinish"),
                    total_float_days=round(_int(slack) / TENTHS_OF_MINUTE_PER_DAY, 4)
                    if slack is not None
                    else None,
                    constraint_type=constraint,
                    constraint_date=_date(_text(task, "ConstraintDate"), "ConstraintDate")
                    if constraint
                    else None,
                    delay_note=_text(task, "Notes"),
                )
            )

            for link in task.findall("p:PredecessorLink", _N):
                relationships.append(
                    Relationship(
                        predecessor_id=_text(link, "PredecessorUID") or "?",
                        successor_id=uid,
                        rel_type=_REL.get(_int(_text(link, "Type"), 1), RelType.FS),
                        lag_days=round(_int(_text(link, "LinkLag")) / TENTHS_OF_MINUTE_PER_DAY, 4),
                    )
                )

        project_id = re.sub(r"[^A-Za-z0-9_]+", "_", project_name).strip("_").upper()
        return Schedule(
            project_id=project_id,
            project_name=project_name,
            data_date=data_date,
            source_format=self.format_name,
            activities=activities,
            relationships=relationships,
            calendars=[Calendar(calendar_id="msp_standard", name="Standard")],
        )


# --------------------------------------------------------------------------------------
# Writer: only used to generate sample data and test fixtures.
# --------------------------------------------------------------------------------------

_REL_OUT = {v: k for k, v in _REL.items()}
_CSTR_OUT = {v: k for k, v in MSP_CONSTRAINTS.items()}


def _dt(value: date | None) -> str | None:
    return f"{value.isoformat()}T08:00:00" if value else None


def write_msp_xml(schedule: Schedule) -> bytes:
    root = Element("Project", xmlns=NS)

    def add(parent: Element, tag: str, value: object) -> None:
        if value is not None:
            SubElement(parent, tag).text = str(value)

    add(root, "Name", schedule.project_name)
    add(root, "StatusDate", _dt(schedule.data_date))
    tasks = SubElement(root, "Tasks")
    for a in schedule.activities:
        t = SubElement(tasks, "Task")
        add(t, "UID", a.activity_id)
        add(t, "Name", a.name)
        add(t, "WBS", a.wbs)
        add(t, "Milestone", "1" if a.is_milestone else "0")
        add(t, "Duration", f"PT{a.original_duration_days * 8:g}H0M0S")
        add(t, "RemainingDuration", f"PT{a.remaining_duration_days * 8:g}H0M0S")
        add(t, "PercentComplete", int(a.percent_complete))
        add(t, "ActualStart", _dt(a.actual_start))
        add(t, "ActualFinish", _dt(a.actual_finish))
        add(t, "EarlyStart", _dt(a.early_start))
        add(t, "EarlyFinish", _dt(a.early_finish))
        if a.total_float_days is not None:
            add(t, "TotalSlack", int(a.total_float_days * TENTHS_OF_MINUTE_PER_DAY))
        if a.constraint_type in _CSTR_OUT:
            add(t, "ConstraintType", _CSTR_OUT[a.constraint_type])
            add(t, "ConstraintDate", _dt(a.constraint_date))
        add(t, "Notes", a.delay_note)
        if a.planned_start or a.planned_finish:
            bl = SubElement(t, "Baseline")
            add(bl, "Number", 0)
            add(bl, "Start", _dt(a.planned_start))
            add(bl, "Finish", _dt(a.planned_finish))
        for r in schedule.relationships:
            if r.successor_id == a.activity_id:
                link = SubElement(t, "PredecessorLink")
                add(link, "PredecessorUID", r.predecessor_id)
                add(link, "Type", _REL_OUT[r.rel_type])
                add(link, "LinkLag", int(r.lag_days * TENTHS_OF_MINUTE_PER_DAY))
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")