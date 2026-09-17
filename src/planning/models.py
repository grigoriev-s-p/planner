"""Small domain models shared by capacity and feasibility logic."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .reasons import ReasonCode


@dataclass(frozen=True)
class SprintState:
    sprint_number: int
    team_sp_used: dict[str, float] = field(default_factory=dict)
    engineer_hours_used: dict[str, float] = field(default_factory=dict)
    engineer_team_hours_used: dict[str, dict[str, float]] = field(default_factory=dict)
    completed_tasks: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= self.sprint_number <= 6:
            raise ValueError("sprint_number must be between 1 and 6.")
        for label, values in (
            ("team_sp_used", self.team_sp_used),
            ("engineer_hours_used", self.engineer_hours_used),
        ):
            for entity_id, value in values.items():
                if float(value) < 0:
                    raise ValueError(f"{label}[{entity_id!r}] cannot be negative.")
        for engineer_id, team_values in self.engineer_team_hours_used.items():
            for team_id, value in team_values.items():
                if float(value) < 0:
                    raise ValueError(
                        "engineer_team_hours_used"
                        f"[{engineer_id!r}][{team_id!r}] cannot be negative."
                    )
        for task_id, sprint in self.completed_tasks.items():
            if int(sprint) < 0:
                raise ValueError(
                    f"completed_tasks[{task_id!r}] cannot be negative; use 0 for work "
                    "completed before the planning quarter."
                )


@dataclass(frozen=True)
class TeamCapacity:
    team_id: str
    average_velocity_sp: float
    focus_factor: float
    capacity_sp: float
    history_samples: int


@dataclass(frozen=True)
class EngineerCapacity:
    engineer_id: str
    capacity_rate: float
    nominal_hours: float


@dataclass(frozen=True)
class TeamCapacityCheck:
    team_id: str
    average_velocity_sp: float
    focus_factor: float
    capacity_sp: float
    already_used_sp: float
    available_sp: float
    required_sp: float
    deficit_sp: float
    result: str
    history_samples: int


@dataclass(frozen=True)
class RoleCapacityCheck:
    role: str
    required_hours: float
    available_hours: float
    deficit_hours: float
    remaining_hours_after_sprint: float
    required_technologies: tuple[str, ...]
    matching_engineer_ids: tuple[str, ...]
    result: str


@dataclass(frozen=True)
class EngineerAvailability:
    engineer_id: str
    source_team_id: str
    teams: tuple[str, ...]
    roles: tuple[str, ...]
    capacity_rate: float
    nominal_hours: float
    used_hours: float
    available_hours: float
    skills: tuple[str, ...]
    required_role: str
    required_technologies: tuple[str, ...]
    matches_required_skills: bool


@dataclass(frozen=True)
class DependencyCheck:
    blocking_task_id: str
    blocked_task_id: str
    dependency_type: str | None
    completed_sprint: int | None
    earliest_start_sprint: int | None
    result: str


@dataclass(frozen=True)
class FeasibilityReason:
    code: ReasonCode
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "details": self.details}


@dataclass(frozen=True)
class FeasibilityResult:
    task_id: str
    sprint: int
    can_start: bool
    can_complete: bool
    task: dict[str, Any]
    team_capacity: TeamCapacityCheck | None
    role_capacity: tuple[RoleCapacityCheck, ...]
    engineers: tuple[EngineerAvailability, ...]
    dependencies: tuple[DependencyCheck, ...]
    reasons: tuple[FeasibilityReason, ...]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sprint": self.sprint,
            "can_start": self.can_start,
            "can_complete": self.can_complete,
            "task": self.task,
            "team_capacity": asdict(self.team_capacity) if self.team_capacity else None,
            "role_capacity": [asdict(item) for item in self.role_capacity],
            "engineers": [asdict(item) for item in self.engineers],
            "dependencies": [asdict(item) for item in self.dependencies],
            "reasons": [reason.to_dict() for reason in self.reasons],
            "limitations": list(self.limitations),
        }
