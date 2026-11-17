"""Small, boring helpers that clean messy values from every source format.

Each helper returns None when a value is blank, and raises ValueError when a value is
present but makes no sense. Parsers decide what to do with that error.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from baseline.canonical.models import ConstraintType

_DATE_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%d %b %Y",
]

# Excel stores dates as days since 1899-12-30.
_EXCEL_EPOCH = date(1899, 12, 30)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def parse_date(value: object) -> date | None:
    """Accepts datetime/date objects, Excel serial numbers, and common text formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        if 20000 < value < 80000:  # plausible Excel serial (1954 .. 2119)
            return _EXCEL_EPOCH + timedelta(days=int(value))
        raise ValueError(f"number {value!r} is not a date")

    text = clean_text(value)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}")


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(value)
    if text is None:
        return None
    return float(text.replace(",", ""))


def parse_percent(value: object) -> float | None:
    """Returns 0-100. Handles '45%', 45, 0.45 (Excel percent cells), and '45'."""
    if value is None:
        return None
    if isinstance(value, str):
        text = clean_text(value)
        if text is None:
            return None
        if text.endswith("%"):
            return float(text[:-1].strip())
        number = float(text)
    else:
        number = float(value)
    # Excel percent-formatted cells come through as fractions.
    if 0 < number <= 1 and not float(number).is_integer():
        return round(number * 100, 4)
    return number


def hours_to_days(hours: float | None, hours_per_day: float = 8.0) -> float | None:
    if hours is None:
        return None
    return round(hours / hours_per_day, 4)


# Primavera P6 constraint codes.
P6_CONSTRAINTS = {
    "CS_MSO": ConstraintType.START_ON,
    "CS_MEO": ConstraintType.FINISH_ON,
    "CS_MSOA": ConstraintType.START_NO_EARLIER,
    "CS_MSOB": ConstraintType.START_NO_LATER,
    "CS_MEOA": ConstraintType.FINISH_NO_EARLIER,
    "CS_MEOB": ConstraintType.FINISH_NO_LATER,
    "CS_MANDSTART": ConstraintType.MANDATORY_START,
    "CS_MANDFIN": ConstraintType.MANDATORY_FINISH,
}

# Microsoft Project ConstraintType numbers.
# 0 = As Soon As Possible and 1 = As Late As Possible are not date constraints.
MSP_CONSTRAINTS = {
    2: ConstraintType.START_ON,  # Must Start On
    3: ConstraintType.FINISH_ON,  # Must Finish On
    4: ConstraintType.START_NO_EARLIER,
    5: ConstraintType.START_NO_LATER,
    6: ConstraintType.FINISH_NO_EARLIER,
    7: ConstraintType.FINISH_NO_LATER,
}