"""Explainable RED/ORANGE/YELLOW risk rules for replanning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .diff import ChangeType, PlanDiff
from .models import QUARTER_SPRINTS, PlanState


class AlertLevel(str, Enum):
    RED = "RED"
    ORANGE = "ORANGE"
    YELLOW = "YELLOW"


LEVEL_PRIORITY = {
    AlertLevel.RED: 3,
    AlertLevel.ORANGE: 2,
    AlertLevel.YELLOW: 1,
}


class AlertCode(str, Enum):
    DEADLINE_OUTSIDE_QUARTER = "deadline_outside_quarter"
    ROLE_CAPACITY_DEFICIT = "role_capacity_deficit"
    DEPENDENCY_SHIFT = "dependency_shift"
    BUS_FACTOR = "bus_factor"
    PLAN_SCOPE_CHANGE = "plan_scope_change"
    TASK_MOVED = "task_moved"


@dataclass(frozen=True, slots=True)
class Alert:
    level: AlertLevel
    code: AlertCode
    entity_id: str
    message: str
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """Planner/star-map facts consumed by the policy, without planner coupling."""

    entity_id: str
    forecast_completion_sprint: int | None = None
    deadline_sprint: int = QUARTER_SPRINTS
    role: str | None = None
    role_shortage_hours: float = 0.0
    shortage_sprint: int | None = None
    dependency_shift_sprints: int = 0
    skill: str | None = None
    bus_factor: int | None = None
    skill_needed_sprint: int | None = None

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("entity_id must not be empty")
        if self.deadline_sprint < 1:
            raise ValueError("deadline_sprint must be positive")
        if self.role_shortage_hours < 0:
            raise ValueError("role_shortage_hours must not be negative")
        if self.dependency_shift_sprints < 0:
            raise ValueError("dependency_shift_sprints must not be negative")
        if self.bus_factor is not None and self.bus_factor < 0:
            raise ValueError("bus_factor must not be negative")
        for field_name in (
            "forecast_completion_sprint",
            "shortage_sprint",
            "skill_needed_sprint",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 1:
                raise ValueError(f"{field_name} must be positive when specified")


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    urgent_horizon_sprints: int = 1

    def __post_init__(self) -> None:
        if self.urgent_horizon_sprints < 1:
            raise ValueError("urgent_horizon_sprints must be positive")


def evaluate_risk_signals(
    signals: tuple[RiskSignal, ...],
    completed_through_sprint: int,
    policy: AlertPolicy = AlertPolicy(),
) -> tuple[Alert, ...]:
    """Turn planner/star-map facts into deterministic, explainable alerts."""

    if not 0 <= completed_through_sprint <= QUARTER_SPRINTS:
        raise ValueError(
            f"completed_through_sprint must be between 0 and {QUARTER_SPRINTS}"
        )
    alerts: list[Alert] = []
    urgent_through = completed_through_sprint + policy.urgent_horizon_sprints
    for signal in signals:
        if (
            signal.forecast_completion_sprint is not None
            and signal.forecast_completion_sprint > signal.deadline_sprint
        ):
            alerts.append(
                Alert(
                    AlertLevel.RED,
                    AlertCode.DEADLINE_OUTSIDE_QUARTER,
                    signal.entity_id,
                    "Forecast completion is later than the allowed deadline.",
                    (
                        ("forecast_sprint", str(signal.forecast_completion_sprint)),
                        ("deadline_sprint", str(signal.deadline_sprint)),
                    ),
                )
            )

        if signal.role_shortage_hours > 0:
            alerts.append(
                Alert(
                    AlertLevel.ORANGE,
                    AlertCode.ROLE_CAPACITY_DEFICIT,
                    signal.entity_id,
                    "Required role capacity is unavailable in an upcoming sprint.",
                    (
                        ("role", signal.role or "unknown"),
                        ("shortage_hours", str(signal.role_shortage_hours)),
                        ("sprint", str(signal.shortage_sprint or "unknown")),
                    ),
                )
            )

        if signal.dependency_shift_sprints > 0:
            alerts.append(
                Alert(
                    AlertLevel.YELLOW,
                    AlertCode.DEPENDENCY_SHIFT,
                    signal.entity_id,
                    "A dependency chain shifted to a later sprint.",
                    (("shift_sprints", str(signal.dependency_shift_sprints)),),
                )
            )

        if signal.bus_factor is not None and signal.bus_factor <= 1:
            urgent = (
                signal.bus_factor == 0
                or (
                    signal.skill_needed_sprint is not None
                    and signal.skill_needed_sprint <= urgent_through
                )
            )
            alerts.append(
                Alert(
                    AlertLevel.RED if urgent else AlertLevel.ORANGE,
                    AlertCode.BUS_FACTOR,
                    signal.entity_id,
                    "A required competency depends on at most one available engineer.",
                    (
                        ("skill", signal.skill or "unknown"),
                        ("bus_factor", str(signal.bus_factor)),
                        ("needed_sprint", str(signal.skill_needed_sprint or "unknown")),
                    ),
                )
            )

    return sort_alerts(alerts)


def alerts_from_plan_diff(plan_diff: PlanDiff) -> tuple[Alert, ...]:
    alerts: list[Alert] = []
    for entry in plan_diff.entries:
        if ChangeType.REMOVED in entry.changes:
            alerts.append(
                Alert(
                    AlertLevel.ORANGE,
                    AlertCode.PLAN_SCOPE_CHANGE,
                    entry.task_id,
                    "A task was removed from the current plan.",
                )
            )
            continue
        if ChangeType.ADDED in entry.changes:
            alerts.append(
                Alert(
                    AlertLevel.YELLOW,
                    AlertCode.PLAN_SCOPE_CHANGE,
                    entry.task_id,
                    "A task was added after the previous plan snapshot.",
                )
            )
            continue
        if entry.after is None or entry.before is None:
            continue
        if (
            ChangeType.STATE_CHANGED in entry.changes
            and entry.after.state
            in {PlanState.DEFERRED, PlanState.MOVED, PlanState.CANCELLED}
        ):
            alerts.append(
                Alert(
                    AlertLevel.ORANGE,
                    AlertCode.PLAN_SCOPE_CHANGE,
                    entry.task_id,
                    f"Task state changed to {entry.after.state.value}.",
                    (("previous_state", entry.before.state.value),),
                )
            )
        elif (
            ChangeType.SPRINT_CHANGED in entry.changes
            and entry.before.completion_sprint is not None
            and entry.after.completion_sprint is not None
            and entry.after.completion_sprint > entry.before.completion_sprint
        ):
            alerts.append(
                Alert(
                    AlertLevel.YELLOW,
                    AlertCode.TASK_MOVED,
                    entry.task_id,
                    "Task completion moved to a later sprint.",
                    (
                        ("from_sprint", str(entry.before.completion_sprint)),
                        ("to_sprint", str(entry.after.completion_sprint)),
                    ),
                )
            )
    return sort_alerts(alerts)


def sort_alerts(alerts: tuple[Alert, ...] | list[Alert]) -> tuple[Alert, ...]:
    return tuple(
        sorted(
            alerts,
            key=lambda alert: (
                -LEVEL_PRIORITY[alert.level],
                alert.entity_id,
                alert.code.value,
            ),
        )
    )
