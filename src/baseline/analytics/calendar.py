"""Working-day arithmetic.

CPM works in whole working days, not calendar days. This class turns dates into
working-day numbers (index) and back, skipping weekends and holidays.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta

from baseline.canonical.models import Calendar

YEARS_BEFORE = 5
YEARS_AFTER = 10


class WorkCalendar:
    def __init__(self, anchor: date, calendar: Calendar | None = None):
        cal = calendar or Calendar(calendar_id="default")
        work_days, holidays = set(cal.work_days), set(cal.holidays)
        start = anchor - timedelta(days=365 * YEARS_BEFORE)
        end = anchor + timedelta(days=365 * YEARS_AFTER)
        self._dates: list[date] = []
        d = start
        while d <= end:
            if d.weekday() in work_days and d not in holidays:
                self._dates.append(d)
            d += timedelta(days=1)

    def index(self, d: date) -> int:
        """Working-day number of d. A weekend/holiday maps to the next working day."""
        i = bisect_left(self._dates, d)
        if i >= len(self._dates):
            raise ValueError(f"{d} is outside the calendar range")
        return i

    def date(self, i: int) -> date:
        i = max(0, min(i, len(self._dates) - 1))
        return self._dates[i]