"""Excel progress sheets from trade contractors.

These are the messiest files in the project. A typical one has:
- a merged title row and a few "Project: ... / Trade: ..." label rows at the top
- the real header row somewhere in the first 15 rows, with varying column names
- dates as real Excel dates, or text like "03/01/2026" or "1-Mar-26"
- percent as 45, "45%", or 0.45
- some rows with no activity ID at all

This parser finds the header, maps the column names, and cleans each value.
It does NOT decide whether the file is acceptable. That's the contract's job.
"""

from __future__ import annotations

import io
import re
import zipfile

from openpyxl import load_workbook

from baseline.canonical.models import ProgressReport, ProgressRow
from baseline.canonical.normalize import clean_text, parse_date, parse_percent
from baseline.parsers.base import ParseError, Parser

# Every column name we've seen for each field, lower-cased.
COLUMN_ALIASES: dict[str, set[str]] = {
    "activity_id": {"activity id", "act id", "activity", "id", "task id", "activity code"},
    "description": {"description", "activity name", "task name", "name", "task"},
    "reported_pct": {"% complete", "% comp", "percent complete", "pct", "progress", "%"},
    "report_date": {"report date", "date", "as of", "status date"},
    "remark": {"remarks", "remark", "comments", "comment", "notes"},
}
HEADER_SEARCH_ROWS = 15


def _norm_header(value: object) -> str:
    text = clean_text(value) or ""
    return re.sub(r"[\s:]+", " ", text.lower()).strip()


def _match_columns(row: tuple) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        name = _norm_header(cell)
        for field, aliases in COLUMN_ALIASES.items():
            if name in aliases and field not in mapping:
                mapping[field] = idx
    return mapping


def _label_value(rows: list[tuple], labels: set[str]) -> str | None:
    """Find 'Project: X' style labels, either in one cell or split across two cells."""
    for row in rows:
        cells = [clean_text(c) for c in row]
        for i, cell in enumerate(cells):
            if not cell:
                continue
            lower = cell.lower()
            for label in labels:
                if lower.startswith(label + ":"):
                    value = cell.split(":", 1)[1].strip()
                    if value:
                        return value
                    nxt = next((c for c in cells[i + 1 :] if c), None)
                    return nxt
                if lower == label:
                    return next((c for c in cells[i + 1 :] if c), None)
    return None


class ProgressXlsxParser(Parser):
    format_name = "progress_xlsx"

    def parse(self, content: bytes, filename: str) -> ProgressReport:
        try:
            wb = load_workbook(io.BytesIO(content), data_only=True)
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            raise ParseError("MALFORMED_FILE", f"not a readable .xlsx ({exc})") from exc

        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ParseError("EMPTY_FILE", "spreadsheet has no rows")

        header_idx, columns = None, {}
        for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
            columns = _match_columns(row)
            if "activity_id" in columns and "reported_pct" in columns:
                header_idx = i
                break
        if header_idx is None:
            raise ParseError(
                "MALFORMED_FILE",
                f"no header row with an activity ID and % complete column "
                f"in the first {HEADER_SEARCH_ROWS} rows",
            )

        top = rows[:header_idx]
        project = _label_value(top, {"project", "project id", "job"})
        trade = _label_value(top, {"trade", "contractor", "subcontractor"}) or "unknown"
        sheet_date = _label_value(top, {"report date", "as of", "week ending"})
        if not project:
            raise ParseError("MISSING_PROJECT", "no 'Project:' label above the header row")

        try:
            default_date = parse_date(sheet_date) if sheet_date else None
        except ValueError as exc:
            raise ParseError("MALFORMED_FILE", f"report date label: {exc}") from exc

        def cell(row: tuple, field: str) -> object:
            idx = columns.get(field)
            return row[idx] if idx is not None and idx < len(row) else None

        out: list[ProgressRow] = []
        for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            if all(clean_text(c) is None for c in row):
                continue  # blank spacer row
            try:
                pct = parse_percent(cell(row, "reported_pct"))
                rdate = parse_date(cell(row, "report_date")) or default_date
            except ValueError as exc:
                raise ParseError("MALFORMED_FILE", f"row {offset}: {exc}") from exc
            out.append(
                ProgressRow(
                    activity_id=clean_text(cell(row, "activity_id")),
                    description=clean_text(cell(row, "description")),
                    reported_pct=pct,
                    report_date=rdate,
                    remark=clean_text(cell(row, "remark")),
                    source_row=offset,
                )
            )

        project_id = re.sub(r"[^A-Za-z0-9_]+", "_", project).strip("_").upper()
        return ProgressReport(project_id=project_id, trade=trade, rows=out)