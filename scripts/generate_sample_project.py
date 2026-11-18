"""Generate a realistic, messy sample data set.

    sample_data/hyperscale_campus/   52 weekly P6 .xer updates for a 2-building data center
        + week_20_resend.xer         the same bytes sent twice           -> duplicate
        + week_33_rev1.xer           a corrected re-issue, same date      -> loaded (bitemporal)
        + week_41_partial.xer        an export cut off halfway            -> quarantined
        + progress/*.xlsx            messy electrical-trade progress sheets
    sample_data/edge_site/           8 weekly MS Project XML updates for a small site

How the simulation works:
1. Build a schedule template and run CPM to get the baseline (planned) dates.
2. Each week, work progresses on activities whose logic allows it.
3. Some activities "slip": their remaining duration grows. Each activity has a hidden risk
   level, and long-lead equipment (switchgear, transformers, generators) is riskiest.
   Slips push successors, eat float, and eventually move milestones, just like real life.
4. After each week, CPM recalculates dates and the update is written as a file.

Run:  python scripts/generate_sample_project.py [--seed 7] [--weeks 52]
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

from baseline.analytics.cpm import compute_cpm
from baseline.canonical.models import (
    Activity,
    ActivityStatus,
    ActivityType,
    Calendar,
    ConstraintType,
    Relationship,
    RelType,
    Schedule,
)
from baseline.parsers.msp_xml import write_msp_xml
from baseline.parsers.xer import write_xer

ROOT = Path(__file__).resolve().parents[1] / "sample_data"
START = date(2025, 9, 1)  # a Monday
DURATION_SCALE = 1.4  # stretch template durations to a ~15-month program
RISK_SCALE = 1.6  # how often risky work slips

PROCUREMENT_NOTES = {
    "P300": [
        "Switchgear vendor pushed delivery after factory acceptance test failure",
        "Switchgear shipment waiting on breaker components",
    ],
    "P310": [
        "Transformer shipment held at port awaiting customs release",
        "Transformer core steel shortage at the factory",
    ],
    "P320": [
        "Generator supplier capacity constrained; delivery slot moved",
        "Generator enclosures failed QA at the vendor",
    ],
    "P330": ["Chiller delivery delayed by vendor backlog"],
    "P340": ["UPS modules on backorder from manufacturer"],
}
FIELD_NOTES = [
    "Rain days lost on excavation",
    "Steel erection crew shortage this week",
    "RFI on containment layout still open with design team",
    "Failed inspection; rework required before close-up",
    "Utility interconnect approval delayed by the utility",
    "Façade panels arrived damaged; replacements ordered",
    "Crane down for repairs, lifts rescheduled",
]


def _a(aid: str, name: str, dur: float, wbs: str, risk: float, **kw) -> dict:
    return {"id": aid, "name": name, "dur": round(dur * DURATION_SCALE), "wbs": wbs,
            "risk": min(risk * RISK_SCALE, 0.5), **kw}  # fmt: skip


def campus_template() -> tuple[list[dict], list[tuple]]:
    """Activities (with hidden risk) and logic for a two-building hyperscale campus."""
    acts = [
        _a("M000", "Notice to proceed", 0, "MS", 0, type=ActivityType.START_MILESTONE),
        _a("S100", "Site clearing", 10, "SITE", 0.03),
        _a("S110", "Mass grading", 15, "SITE", 0.05),
        _a("S120", "Underground utilities", 20, "SITE", 0.05),
        _a("P300", "Switchgear fabrication and delivery", 95, "PROC", 0.16),
        _a("P310", "Transformer fabrication and delivery", 110, "PROC", 0.14),
        _a("P320", "Generator fabrication and delivery", 100, "PROC", 0.12),
        _a("P330", "Chiller fabrication and delivery", 80, "PROC", 0.08),
        _a("P340", "UPS fabrication and delivery", 70, "PROC", 0.06),
        _a("E400", "Substation civil and structure", 40, "POWER", 0.05),
        _a("E410", "Utility interconnect", 30, "POWER", 0.10),
        _a("E420", "Set and test main transformers", 10, "POWER", 0.04),
        _a("E450", "Set and test generators", 15, "POWER", 0.04),
    ]
    rels = [
        ("M000", "S100", RelType.FS, 0), ("S100", "S110", RelType.FS, 0),
        ("S110", "S120", RelType.FS, 0), ("M000", "P300", RelType.FS, 0),
        ("M000", "P310", RelType.FS, 0), ("M000", "P320", RelType.FS, 0),
        ("M000", "P330", RelType.FS, 0), ("M000", "P340", RelType.FS, 0),
        ("S120", "E400", RelType.FS, 0), ("E400", "E410", RelType.FS, 0),
        ("E400", "E420", RelType.FS, 0), ("P310", "E420", RelType.FS, 0),
        ("P320", "E450", RelType.FS, 0), ("E400", "E450", RelType.FS, 0),
    ]  # fmt: skip
    for b, lag in (("A", 0), ("B", 25)):
        acts += [
            _a(f"{b}200", f"Building {b} foundations", 25, f"BLD{b}", 0.05),
            _a(f"{b}210", f"Building {b} steel erection", 30, f"BLD{b}", 0.07),
            _a(f"{b}220", f"Building {b} roofing", 15, f"BLD{b}", 0.05),
            _a(f"{b}230", f"Building {b} envelope and façade", 25, f"BLD{b}", 0.06),
            _a(f"{b}430", f"Building {b} install switchgear", 15, f"BLD{b}", 0.05),
            _a(f"{b}440", f"Building {b} medium-voltage feeders", 20, f"BLD{b}", 0.05),
            _a(f"{b}500", f"Building {b} install chillers", 20, f"BLD{b}", 0.05),
            _a(f"{b}510", f"Building {b} CRAH units", 15, f"BLD{b}", 0.04),
            _a(f"{b}520", f"Building {b} chilled water piping", 25, f"BLD{b}", 0.05),
            _a(f"{b}600", f"Building {b} raised floor and containment", 20, f"BLD{b}", 0.04),
            _a(f"{b}610", f"Building {b} cable tray", 15, f"BLD{b}", 0.03),
            _a(f"{b}620", f"Building {b} UPS installation", 10, f"BLD{b}", 0.04),
            _a(f"{b}700", f"Building {b} L2 installation checks", 10, f"CX{b}", 0.03),
            _a(f"{b}710", f"Building {b} L3 startup", 15, f"CX{b}", 0.05),
            _a(f"{b}720", f"Building {b} L4 functional testing", 15, f"CX{b}", 0.06),
            _a(f"{b}730", f"Building {b} L5 integrated systems test", 10, f"CX{b}", 0.06),
            _a(f"{b}800", f"Building {b} energized", 0, "MS", 0,
               type=ActivityType.FINISH_MILESTONE),
            _a(f"{b}900", f"Building {b} ready for service", 0, "MS", 0,
               type=ActivityType.FINISH_MILESTONE),
        ]  # fmt: skip
        rels += [
            ("S120", f"{b}200", RelType.SS, 10 + lag), (f"{b}200", f"{b}210", RelType.FS, 0),
            (f"{b}210", f"{b}220", RelType.FS, 0), (f"{b}210", f"{b}230", RelType.SS, 10),
            (f"{b}220", f"{b}430", RelType.FS, 0), ("P300", f"{b}430", RelType.FS, 0),
            (f"{b}430", f"{b}440", RelType.FS, 0), ("E420", f"{b}440", RelType.FS, 0),
            (f"{b}440", f"{b}800", RelType.FS, 0), ("E410", f"{b}800", RelType.FS, 0),
            ("P330", f"{b}500", RelType.FS, 0), (f"{b}220", f"{b}500", RelType.FS, 0),
            (f"{b}500", f"{b}520", RelType.FS, 0), (f"{b}230", f"{b}510", RelType.FS, 0),
            (f"{b}230", f"{b}600", RelType.FS, 0), (f"{b}600", f"{b}610", RelType.FS, 0),
            (f"{b}610", f"{b}620", RelType.FS, 0), ("P340", f"{b}620", RelType.FS, 0),
            (f"{b}800", f"{b}700", RelType.FS, 0), (f"{b}520", f"{b}700", RelType.FS, 0),
            (f"{b}510", f"{b}700", RelType.FS, 0), (f"{b}620", f"{b}700", RelType.FS, 0),
            ("E450", f"{b}710", RelType.FS, 0), (f"{b}700", f"{b}710", RelType.FS, 0),
            (f"{b}710", f"{b}720", RelType.FS, 0), (f"{b}720", f"{b}730", RelType.FS, 0),
            (f"{b}730", f"{b}900", RelType.FS, 0),
        ]  # fmt: skip
    return acts, rels


def edge_template() -> tuple[list[dict], list[tuple]]:
    acts = [
        _a("1", "Mobilize", 5, "EDGE", 0.02),
        _a("2", "Pad and foundations", 15, "EDGE", 0.06),
        _a("3", "Prefab module delivery", 40, "EDGE", 0.15),
        _a("4", "Set modules", 5, "EDGE", 0.05),
        _a("5", "Power and fiber hookup", 10, "EDGE", 0.08),
        _a("6", "Commissioning", 10, "EDGE", 0.05),
        _a("7", "Edge site live", 0, "EDGE", 0, type=ActivityType.FINISH_MILESTONE),
    ]
    rels = [("1", "2", RelType.FS, 0), ("1", "3", RelType.FS, 0), ("2", "4", RelType.FS, 0),
            ("3", "4", RelType.FS, 0), ("4", "5", RelType.FS, 0), ("5", "6", RelType.FS, 0),
            ("6", "7", RelType.FS, 0)]  # fmt: skip
    return acts, rels


class Simulation:
    def __init__(self, project_id: str, name: str, template, start: date, seed: int):
        self.rng = random.Random(seed)
        self.project_id, self.name = project_id, name
        spec, rels = template
        self.spec = {s["id"]: s for s in spec}
        self.rels = [Relationship(predecessor_id=p, successor_id=s, rel_type=t, lag_days=lag)
                     for p, s, t, lag in rels]  # fmt: skip
        self.calendar = Calendar(calendar_id="1", name="5-day week")
        self.data_date = start
        self.activities = {
            s["id"]: Activity(
                activity_id=s["id"], name=s["name"], wbs=s["wbs"],
                activity_type=s.get("type", ActivityType.TASK),
                original_duration_days=s["dur"], remaining_duration_days=s["dur"],
                calendar_id="1",
            )
            for s in spec
        }  # fmt: skip
        # Baseline: plan dates from the very first CPM run.
        base = compute_cpm(self.schedule())
        for aid, a in self.activities.items():
            a.planned_start = base.start_date(aid)
            a.planned_finish = base.finish_date(aid)
            if a.activity_type == ActivityType.FINISH_MILESTONE:
                # Contract dates: the baseline finish plus 10 working days of buffer.
                a.constraint_type = ConstraintType.FINISH_NO_LATER
                a.constraint_date = base.calendar.date(base.activities[aid].early_finish_day + 10)
        self.refresh_dates()

    def schedule(self) -> Schedule:
        return Schedule(
            project_id=self.project_id, project_name=self.name, data_date=self.data_date,
            source_format="sim", activities=list(self.activities.values()),
            relationships=self.rels, calendars=[self.calendar],
        )  # fmt: skip

    def refresh_dates(self):
        cpm = compute_cpm(self.schedule())
        for aid, a in self.activities.items():
            r = cpm.activities[aid]
            a.early_start, a.early_finish = cpm.start_date(aid), cpm.finish_date(aid)
            a.late_start = cpm.calendar.date(r.ls)
            a.late_finish = cpm.calendar.date(r.late_finish_day)
            a.total_float_days = r.total_float
        return cpm

    def step_week(self):
        cpm = compute_cpm(self.schedule())
        cal, dd = cpm.calendar, cpm.data_date_index
        week_end = dd + 5
        for aid, a in self.activities.items():
            if a.status == ActivityStatus.COMPLETE:
                continue
            r = cpm.activities[aid]
            if r.es >= week_end:
                continue  # logic doesn't allow it to start this week
            if a.is_milestone:
                a.status = ActivityStatus.COMPLETE
                day = cal.date(r.early_finish_day)
                a.actual_start = a.actual_finish = day
                a.remaining_duration_days, a.percent_complete = 0, 100
                continue

            if a.status == ActivityStatus.NOT_STARTED:
                a.status = ActivityStatus.IN_PROGRESS
                a.actual_start = cal.date(r.es)
            a.delay_note = None

            # Hidden risk: risky work slips again and again (that's what the model learns).
            risk = self.spec[aid]["risk"]
            if self.rng.random() < risk:
                growth = self.rng.choice([5, 8, 10, 15, 20] if aid.startswith("P") else [3, 5, 8])
                a.remaining_duration_days += growth
                a.original_duration_days += growth if self.rng.random() < 0.3 else 0
                notes = PROCUREMENT_NOTES.get(aid, FIELD_NOTES)
                a.delay_note = self.rng.choice(notes)

            first_day = max(r.es, dd)
            worked = week_end - first_day
            left_before = a.remaining_duration_days
            a.remaining_duration_days = max(0.0, left_before - worked)
            done = 1 - a.remaining_duration_days / max(a.original_duration_days, 1)
            a.percent_complete = round(max(0.0, min(100.0, done * 100)), 1)
            if a.remaining_duration_days == 0:
                a.status = ActivityStatus.COMPLETE
                a.percent_complete = 100
                finish_day = first_day + max(1, math.ceil(left_before)) - 1
                a.actual_finish = cal.date(max(finish_day, cal.index(a.actual_start)))

        self.data_date += timedelta(days=7)
        self.refresh_dates()


def write_progress_sheet(path: Path, sim: Simulation, rng: random.Random, missing_ids: int):
    """A deliberately messy trade sheet: merged title, labels, mixed formats."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Progress"
    ws.merge_cells("A1:E1")
    ws["A1"] = "VOLT ELECTRICAL CONTRACTORS - WEEKLY PROGRESS REPORT"
    ws.append(["Project:", sim.project_id])
    ws.append(["Trade: Electrical"])
    ws.append([f"Week ending: {sim.data_date:%m/%d/%Y}"])
    ws.append([])
    ws.append(["Act ID", "Description", "% Comp", "Report Date", "Remarks"])
    elec = [a for a in sim.activities.values()
            if a.activity_id[1:] in ("430", "440", "610", "620") or a.activity_id[0] == "E"]
    date_styles = [lambda d: d, lambda d: d.strftime("%m/%d/%Y"), lambda d: d.strftime("%d-%b-%y")]
    pct_styles = [lambda p: p, lambda p: f"{p:g}%", lambda p: round(p / 100, 3) or 0]
    for i, a in enumerate(elec):
        ws.append([
            None if i < missing_ids else a.activity_id,
            a.name,
            rng.choice(pct_styles)(a.percent_complete),
            rng.choice(date_styles)(sim.data_date),
            "Waiting on gear" if a.delay_note else "",
        ])  # fmt: skip
    wb.save(path)


def generate(seed: int = 7, weeks: int = 52, root: Path = ROOT) -> None:
    campus_dir = root / "hyperscale_campus"
    progress_dir = campus_dir / "progress"
    edge_dir = root / "edge_site"
    for d in (campus_dir, progress_dir, edge_dir):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.*"):
            f.unlink()

    rng = random.Random(seed + 1)
    sim = Simulation("HYPERSCALE_CAMPUS", "Hyperscale Campus", campus_template(), START, seed)
    for week in range(1, weeks + 1):
        sim.step_week()
        data = write_xer(sim.schedule())
        name = f"week_{week:02d}.xer"
        (campus_dir / name).write_bytes(data)

        if week == 20:
            (campus_dir / "week_20_resend.xer").write_bytes(data)
        if week == 33:
            fixed = sim.schedule().model_copy(deep=True)
            fixed.activities[1].delay_note = "Corrected update: site clearing closeout paperwork"
            (campus_dir / "week_33_rev1.xer").write_bytes(write_xer(fixed))
        if week == 41:
            (campus_dir / "week_41_partial.xer").write_bytes(data[: len(data) // 2])
        if week % 4 == 0:
            missing = 4 if week == 24 else (1 if week % 8 == 0 else 0)  # week 24: too many
            write_progress_sheet(
                progress_dir / f"electrical_{sim.data_date:%Y-%m-%d}.xlsx", sim, rng, missing
            )

    edge = Simulation("EDGE_SITE", "Edge Site", edge_template(), date(2026, 1, 5), seed + 2)
    for week in range(1, 9):
        edge.step_week()
        (edge_dir / f"edge_week_{week:02d}.xml").write_bytes(write_msp_xml(edge.schedule()))

    files = sorted(p.relative_to(root) for p in root.rglob("*.*"))
    print(f"wrote {len(files)} files under {root}")
    final = compute_cpm(sim.schedule())
    print(f"campus forecast finish after {weeks} weeks: {final.project_finish}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--weeks", type=int, default=52)
    args = parser.parse_args()
    generate(args.seed, args.weeks)