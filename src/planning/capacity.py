"""Capacity calculations defined by the case specification."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .models import EngineerCapacity, TeamCapacity


FOCUS_FACTOR = 0.8
SPRINT_HOURS = 80.0


def _required_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric, got {value!r}.") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite, got {value!r}.")
    return number


def calculate_team_capacity(
    team_id: str,
    team_history: Iterable[Mapping[str, Any]],
    focus_factor: float = FOCUS_FACTOR,
) -> TeamCapacity:
    """Return mean achieved velocity multiplied by the mandatory focus factor."""

    factor = _required_number(focus_factor, "focus_factor")
    if factor < 0:
        raise ValueError("focus_factor cannot be negative.")
    velocities: list[float] = []
    for row in team_history:
        if str(row.get("team_id", "")).strip() != team_id:
            continue
        value = row.get("velocity_achieved")
        try:
            velocity = _required_number(value, "velocity_achieved")
        except ValueError:
            continue
        if velocity >= 0:
            velocities.append(velocity)
    average = sum(velocities) / len(velocities) if velocities else 0.0
    return TeamCapacity(
        team_id=team_id,
        average_velocity_sp=average,
        focus_factor=factor,
        capacity_sp=average * factor,
        history_samples=len(velocities),
    )


def calculate_engineer_capacity(engineer: Mapping[str, Any]) -> EngineerCapacity:
    """Return one engineer-team profile row's per-sprint capacity."""

    engineer_id = str(engineer.get("engineer_id", "")).strip()
    if not engineer_id:
        raise ValueError("engineer_id is required.")
    capacity_rate = _required_number(engineer.get("capacity_rate"), "capacity_rate")
    if not 0 < capacity_rate <= 1:
        raise ValueError("capacity_rate must be in (0, 1].")
    return EngineerCapacity(
        engineer_id=engineer_id,
        capacity_rate=capacity_rate,
        nominal_hours=SPRINT_HOURS * capacity_rate,
    )
