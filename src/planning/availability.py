"""Canonical engineer availability constraints for planning capacity."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


ALLOWED_AVAILABILITY_RATES = frozenset({0.0, 0.5, 1.0})


def validate_availability_rate(value: Any) -> float:
    """Return a supported availability rate or raise a stable validation error."""

    try:
        rate = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("availability_rate must be one of: 0, 0.5, 1") from error
    if rate not in ALLOWED_AVAILABILITY_RATES:
        raise ValueError("availability_rate must be one of: 0, 0.5, 1")
    return rate


def availability_rate_index(
    rows: Iterable[Mapping[str, Any]],
) -> dict[int, dict[str, float]]:
    """Build one in-memory ``sprint -> engineer -> rate`` lookup.

    Several sources may constrain the same person later (for example MANUAL
    and TRAINING).  Constraints compose by taking the lowest rate, so adding a
    source can never increase the person's base capacity.
    """

    result: dict[int, dict[str, float]] = {}
    for row in rows:
        engineer_id = str(row.get("engineer_id") or "").strip()
        if not engineer_id:
            continue
        try:
            sprint = int(row.get("sprint") or 0)
            rate = validate_availability_rate(row.get("availability_rate"))
        except (TypeError, ValueError):
            continue
        if not 1 <= sprint <= 6:
            continue
        current = result.setdefault(sprint, {}).get(engineer_id, 1.0)
        result[sprint][engineer_id] = min(current, rate)
    return result


def effective_capacity_hours(base_capacity_hours: float, availability_rate: Any = 1.0) -> float:
    """Apply an availability constraint without ever increasing base capacity."""

    rate = validate_availability_rate(availability_rate)
    return max(float(base_capacity_hours), 0.0) * min(rate, 1.0)
