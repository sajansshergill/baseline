"""Primavera P6 .xer reader (and a small writer used to make sample files).

An XER file is plain text, tab-separated:

    ERMHDR  19.12  ...          <- header line
    %T  TASK                    <- a table starts
    %F  task_id  task_code ...  <- its column names
    %R  1001     A1000     ...  <- one row
    %R  ...
    %E                          <- end of file

If the %E line is missing, the export was cut off and we refuse the file.
"""

from __future__ import annotations

import re
from datetime import date

from baseline.canonical.models import (
    Activity,
    ActivityStatus,
    ActivityType,
    Calendar,
    Relationship,
    RelType,
    Schedule,
)
from baseline.canonical.normalize import (
    P6_CONSTRAINTS,
    clean_text,
    hours_to_days,
    parse_date,
    parse_float,
)
from baseline.parsers.base import ParseError, Parser
from baseline.parsers.encoding import decode_text

_STATUS = {
    "TK_NotStart": ActivityStatus.NOT_STARTED,
    "TK_Active": ActivityStatus.IN_PROGRESS,
    "TK_Complete": ActivityStatus.COMPLETE,
}
_TYPE = {
    "TT_Mile": ActivityType.START_MILESTONE,
    "TT_FinMile": ActivityType.FINISH_MILESTONE,
}
_REL = {"PR_FS": RelType.FS, "PR_SS": RelType.SS, "PR_FF": RelType.FF, "PR_SF": RelType.SF}


def read_tables(text: str) -> dict[str, list[dict[str, str]]]:
    """Split XER text into {table_name: [row_dict, ...]}."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("ERMHDR"):
        raise ParseError("MALFORMED_FILE", "missing ERMHDR header; not a P6 export")
    if not any(line.strip() == "%E" for line in lines[-5:]):
        raise ParseError("TRUNCATED_FILE", "no %E end marker; the export was cut off")

    tables: dict[str, list[dict[str, str]]] = {}
    current: str | None = None
    fields: list[str] = []

    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split("\t")
        marker = parts[0]
        if marker == "%T":
            current = parts[1].strip()
            tables[current] = []
            fields = []
        elif marker == "%F":
            fields = [f.strip() for f in parts[1:]]
        elif marker == "%R":
            if current is None or not fields:
                raise ParseError("MALFORMED_FILE", f"line {line_no}: row before table header")
            values = parts[1:]
            if len(values) != len(fields):
                raise ParseError(
                    "MALFORMED_FILE",
                    f"line {line_no}: table {current} has {len(fields)} columns, "
                    f"row has {len(values)}",
                )
            tables[current].append(dict(zip(fields, values, strict=True)))
        elif marker == "%E":
            break
    return tables


def _date(row: dict[str, str], field: str, table: str) -> date | None:
    try:
        return parse_date(row.get(field))
    except ValueError as exc:
        raise ParseError("MALFORMED_FILE", f"{table}.{field}: {exc}") from exc


def _num(row: dict[str, str], field: str, table: str) -> float | None:
    try:
        return parse_float(row.get(field))
    except ValueError as exc:
        raise ParseError("MALFORMED_FILE", f"{table}.{field}: {exc}") from exc


def _strip_html(text: str | None) -> str | None:
    if text is None:
        return None
    return clean_text(re.sub(r"<[^>]+>", " ", text))


class XerParser(Parser):
    format_name = "p6_xer"

    def parse(self, content: bytes, filename: str) -> Schedule:
        tables = read_tables(decode_text(content))

        for required in ("PROJECT", "TASK"):
            if required not in tables:
                raise ParseError("MALFORMED_FILE", f"missing {required} table")
        if len(tables["PROJECT"]) != 1:
            raise ParseError(
                "MALFORMED_FILE", f"expected 1 project, found {len(tables['PROJECT'])}"
            )
        project = tables["PROJECT"][0]

        calendars: list[Calendar] = []
        hours_per_day: dict[str, float] = {}
        for row in tables.get("CALENDAR", []):
            hpd = _num(row, "day_hr_cnt", "CALENDAR") or 8.0
            hours_per_day[row["clndr_id"]] = hpd
            calendars.append(
                Calendar(
                    calendar_id=row["clndr_id"],
                    name=clean_text(row.get("clndr_name")) or "Calendar",
                    hours_per_day=hpd,
                )
            )

        notes: dict[str, str] = {}
        for row in tables.get("TASKMEMO", []):
            text = _strip_html(row.get("task_memo"))
            if text:
                notes[row["task_id"]] = text

        code_by_internal_id: dict[str, str] = {}
        activities: list[Activity] = []
        for row in tables["TASK"]:
            internal_id = row.get("task_id", "")
            code = clean_text(row.get("task_code")) or ""
            code_by_internal_id[internal_id] = code
            hpd = hours_per_day.get(row.get("clndr_id", ""), 8.0)

            constraint = P6_CONSTRAINTS.get((row.get("cstr_type") or "").strip())
            activities.append(
                Activity(
                    activity_id=code,
                    name=clean_text(row.get("task_name")) or code,
                    wbs=clean_text(row.get("wbs_id")),
                    activity_type=_TYPE.get(row.get("task_type", ""), ActivityType.TASK),
                    status=_STATUS.get(row.get("status_code", ""), ActivityStatus.NOT_STARTED),
                    percent_complete=_num(row, "phys_complete_pct", "TASK") or 0.0,
                    original_duration_days=hours_to_days(
                        _num(row, "target_drtn_hr_cnt", "TASK"), hpd
                    )
                    or 0.0,
                    remaining_duration_days=hours_to_days(
                        _num(row, "remain_drtn_hr_cnt", "TASK"), hpd
                    )
                    or 0.0,
                    planned_start=_date(row, "target_start_date", "TASK"),
                    planned_finish=_date(row, "target_end_date", "TASK"),
                    actual_start=_date(row, "act_start_date", "TASK"),
                    actual_finish=_date(row, "act_end_date", "TASK"),
                    early_start=_date(row, "early_start_date", "TASK"),
                    early_finish=_date(row, "early_end_date", "TASK"),
                    late_start=_date(row, "late_start_date", "TASK"),
                    late_finish=_date(row, "late_end_date", "TASK"),
                    total_float_days=hours_to_days(_num(row, "total_float_hr_cnt", "TASK"), hpd),
                    constraint_type=constraint,
                    constraint_date=_date(row, "cstr_date", "TASK") if constraint else None,
                    calendar_id=clean_text(row.get("clndr_id")),
                    delay_note=notes.get(internal_id),
                )
            )

        relationships: list[Relationship] = []
        for row in tables.get("TASKPRED", []):
            pred = code_by_internal_id.get(row.get("pred_task_id", ""))
            succ = code_by_internal_id.get(row.get("task_id", ""))
            # An unknown internal id is kept as-is so the contract check can flag it.
            relationships.append(
                Relationship(
                    predecessor_id=pred or f"?{row.get('pred_task_id')}",
                    successor_id=succ or f"?{row.get('task_id')}",
                    rel_type=_REL.get(row.get("pred_type", ""), RelType.FS),
                    lag_days=hours_to_days(_num(row, "lag_hr_cnt", "TASKPRED"), 8.0) or 0.0,
                )
            )

        return Schedule(
            project_id=clean_text(project.get("proj_short_name")) or "UNKNOWN",
            project_name=clean_text(project.get("proj_short_name")) or "UNKNOWN",
            data_date=_date(project, "last_recalc_date", "PROJECT"),
            source_format=self.format_name,
            activities=activities,
            relationships=relationships,
            calendars=calendars,
        )


# --------------------------------------------------------------------------------------
# Writer: only used to generate sample data and test fixtures.
# --------------------------------------------------------------------------------------

_STATUS_OUT = {v: k for k, v in _STATUS.items()}
_TYPE_OUT = {ActivityType.TASK: "TT_Task", **{v: k for k, v in _TYPE.items()}}
_REL_OUT = {v: k for k, v in _REL.items()}
_CSTR_OUT = {v: k for k, v in P6_CONSTRAINTS.items()}


def _d(value: date | None) -> str:
    return f"{value.isoformat()} 08:00" if value else ""


def _n(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def write_xer(schedule: Schedule, encoding: str = "cp1252") -> bytes:
    hpd = 8.0
    out: list[str] = ["ERMHDR\t19.12\t" + _d(schedule.data_date)[:10] + "\tProject\tbaseline"]

    def table(name: str, fields: list[str], rows: list[list[str]]) -> None:
        out.append(f"%T\t{name}")
        out.append("%F\t" + "\t".join(fields))
        for r in rows:
            out.append("%R\t" + "\t".join(r))

    table(
        "PROJECT",
        ["proj_id", "proj_short_name", "last_recalc_date"],
        [["1", schedule.project_id, _d(schedule.data_date)]],
    )
    cals = schedule.calendars or [Calendar(calendar_id="1")]
    table(
        "CALENDAR",
        ["clndr_id", "clndr_name", "day_hr_cnt"],
        [[c.calendar_id, c.name, _n(c.hours_per_day)] for c in cals],
    )

    internal = {a.activity_id: str(1000 + i) for i, a in enumerate(schedule.activities)}
    task_fields = [
        "task_id", "proj_id", "wbs_id", "clndr_id", "task_code", "task_name", "task_type",
        "status_code", "phys_complete_pct", "target_drtn_hr_cnt", "remain_drtn_hr_cnt",
        "total_float_hr_cnt", "target_start_date", "target_end_date", "act_start_date",
        "act_end_date", "early_start_date", "early_end_date", "late_start_date",
        "late_end_date", "cstr_type", "cstr_date",
    ]  # fmt: skip
    task_rows = []
    for a in schedule.activities:
        task_rows.append(
            [
                internal[a.activity_id], "1", a.wbs or "", a.calendar_id or cals[0].calendar_id,
                a.activity_id, a.name, _TYPE_OUT[a.activity_type], _STATUS_OUT[a.status],
                _n(a.percent_complete), _n(a.original_duration_days * hpd),
                _n(a.remaining_duration_days * hpd),
                _n(None if a.total_float_days is None else a.total_float_days * hpd),
                _d(a.planned_start), _d(a.planned_finish), _d(a.actual_start),
                _d(a.actual_finish), _d(a.early_start), _d(a.early_finish), _d(a.late_start),
                _d(a.late_finish), _CSTR_OUT.get(a.constraint_type, "") if a.constraint_type
                else "", _d(a.constraint_date),
            ]
        )  # fmt: skip
    table("TASK", task_fields, task_rows)

    table(
        "TASKPRED",
        ["task_pred_id", "task_id", "pred_task_id", "pred_type", "lag_hr_cnt"],
        [
            [
                str(i + 1),
                internal.get(r.successor_id, r.successor_id),
                internal.get(r.predecessor_id, r.predecessor_id),
                _REL_OUT[r.rel_type],
                _n(r.lag_days * hpd),
            ]
            for i, r in enumerate(schedule.relationships)
        ],
    )
    memos = [a for a in schedule.activities if a.delay_note]
    table(
        "TASKMEMO",
        ["memo_id", "task_id", "task_memo"],
        [
            [str(i + 1), internal[a.activity_id], f"<p>{a.delay_note}</p>"]
            for i, a in enumerate(memos)
        ],
    )
    out.append("%E")
    return ("\r\n".join(out) + "\r\n").encode(encoding, errors="replace")