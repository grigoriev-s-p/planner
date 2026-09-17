"""Optional mapping between six planning sprints and calendar boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


SPRINT_DAYS = 14


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


@dataclass(frozen=True)
class PlanningCalendar:
    quarter_start: date

    @classmethod
    def from_value(cls, value: Any) -> "PlanningCalendar | None":
        parsed = parse_date(value)
        return cls(parsed) if parsed else None

    @property
    def quarter_end(self) -> date:
        return self.quarter_start + timedelta(days=6 * SPRINT_DAYS - 1)

    def sprint_start(self, sprint_number: int) -> date:
        return self.quarter_start + timedelta(days=(sprint_number - 1) * SPRINT_DAYS)

    def sprint_end(self, sprint_number: int) -> date:
        return self.sprint_start(sprint_number) + timedelta(days=SPRINT_DAYS - 1)


def shift_days(forecast: date | None, baseline_value: Any) -> int | None:
    baseline = parse_date(baseline_value)
    return (forecast - baseline).days if forecast and baseline else None


def shift_direction(value: int | None) -> str | None:
    if value is None:
        return None
    if value < 0:
        return "EARLIER"
    if value > 0:
        return "LATER"
    return "ON_BASELINE"

