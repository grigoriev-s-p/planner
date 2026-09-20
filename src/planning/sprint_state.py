"""Mutable resource ledger used internally while constructing a quarter plan."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import FeasibilityReason, SprintState
from .plan_models import SprintPlan, TaskAllocation


@dataclass
class SprintLedger:
    sprint_number: int
    team_capacity_sp: dict[str, float]
    team_sp_used: dict[str, float] = field(default_factory=dict)
    engineer_hours_used: dict[str, float] = field(default_factory=dict)
    engineer_team_hours_used: dict[str, dict[str, float]] = field(default_factory=dict)
    task_allocations: list[TaskAllocation] = field(default_factory=list)
    warnings: list[FeasibilityReason] = field(default_factory=list)

    def clone(self) -> "SprintLedger":
        """Create an isolated ledger for atomic task-placement simulation."""

        return SprintLedger(
            sprint_number=self.sprint_number,
            team_capacity_sp=dict(self.team_capacity_sp),
            team_sp_used=dict(self.team_sp_used),
            engineer_hours_used=dict(self.engineer_hours_used),
            engineer_team_hours_used={
                engineer_id: dict(team_hours)
                for engineer_id, team_hours in self.engineer_team_hours_used.items()
            },
            task_allocations=list(self.task_allocations),
            warnings=list(self.warnings),
        )

    def feasibility_state(
        self,
        completed_tasks: dict[str, int],
        actual_completed_tasks: dict[str, int] | None = None,
    ) -> SprintState:
        return SprintState(
            sprint_number=self.sprint_number,
            team_sp_used=dict(self.team_sp_used),
            engineer_hours_used=dict(self.engineer_hours_used),
            engineer_team_hours_used={
                engineer_id: dict(team_hours)
                for engineer_id, team_hours in self.engineer_team_hours_used.items()
            },
            completed_tasks=dict(completed_tasks),
            actual_completed_tasks=dict(actual_completed_tasks or {}),
        )

    def add_allocation(self, team_id: str, allocation: TaskAllocation) -> None:
        if allocation.capacity_sp_load:
            new_value = self.team_sp_used.get(team_id, 0.0) + allocation.capacity_sp_load
            capacity = self.team_capacity_sp.get(team_id, 0.0)
            if new_value > capacity + 1e-9:
                raise ValueError(
                    f"Team {team_id} SP capacity exceeded in Sprint {self.sprint_number}."
                )
            self.team_sp_used[team_id] = new_value
        for engineer in allocation.engineer_allocations:
            self.engineer_hours_used[engineer.engineer_id] = (
                self.engineer_hours_used.get(engineer.engineer_id, 0.0) + engineer.hours
            )
            if engineer.source_team_id is not None:
                team_hours = self.engineer_team_hours_used.setdefault(
                    engineer.engineer_id, {}
                )
                team_hours[engineer.source_team_id] = (
                    team_hours.get(engineer.source_team_id, 0.0) + engineer.hours
                )
        self.task_allocations.append(allocation)

    def freeze(self) -> SprintPlan:
        return SprintPlan(
            sprint_number=self.sprint_number,
            team_capacity_sp=dict(sorted(self.team_capacity_sp.items())),
            team_sp_used=dict(sorted(self.team_sp_used.items())),
            engineer_hours_used=dict(sorted(self.engineer_hours_used.items())),
            engineer_team_hours_used={
                engineer_id: dict(sorted(team_hours.items()))
                for engineer_id, team_hours in sorted(
                    self.engineer_team_hours_used.items()
                )
            },
            task_allocations=tuple(self.task_allocations),
            warnings=tuple(self.warnings),
        )
