"""Deterministic greedy allocation of remaining role hours to engineers."""

from __future__ import annotations

from dataclasses import dataclass

from .models import FeasibilityResult
from .plan_models import EngineerTaskAllocation


@dataclass(frozen=True)
class AllocationOutcome:
    allocated_role_hours: dict[str, float]
    engineer_allocations: tuple[EngineerTaskAllocation, ...]

    @property
    def total_hours(self) -> float:
        return sum(self.allocated_role_hours.values())


def allocate_available_hours(
    remaining_role_hours: dict[str, float],
    feasibility: FeasibilityResult,
    *,
    max_total_hours: float = float("inf"),
) -> AllocationOutcome:
    """Prefer local engineers, then free hours and engineer_id within each role.

    Availability comes only from the reusable feasibility checker. The local
    per-bucket reservation prevents one engineer-team capacity row from being
    allocated twice to different roles of the same task in this sprint.
    """

    by_role: dict[str, list] = {}
    for engineer in feasibility.engineers:
        if not engineer.matches_required_skills or engineer.available_hours <= 0:
            continue
        by_role.setdefault(engineer.required_role, []).append(engineer)

    assigned_by_bucket: dict[tuple[str, str], float] = {}
    role_allocations: dict[str, float] = {}
    engineer_allocations: list[EngineerTaskAllocation] = []
    task_team = feasibility.team_capacity.team_id if feasibility.team_capacity else None
    role_order = sorted(
        remaining_role_hours,
        key=lambda role: (len(by_role.get(role, [])), role),
    )
    for role in role_order:
        total_remaining_limit = max(max_total_hours - sum(role_allocations.values()), 0.0)
        if total_remaining_limit <= 0:
            role_allocations[role] = 0.0
            continue
        role_remaining = max(float(remaining_role_hours[role]), 0.0)
        role_remaining = min(role_remaining, total_remaining_limit)
        allocated = 0.0
        candidates = sorted(
            by_role.get(role, []),
            key=lambda item: (
                item.source_team_id != task_team,
                -max(
                    item.available_hours
                    - assigned_by_bucket.get(
                        (item.engineer_id, item.source_team_id), 0.0
                    ),
                    0.0,
                ),
                item.engineer_id,
                item.source_team_id,
            ),
        )
        for engineer in candidates:
            bucket = (engineer.engineer_id, engineer.source_team_id)
            already_assigned = assigned_by_bucket.get(bucket, 0.0)
            available = max(engineer.available_hours - already_assigned, 0.0)
            hours = min(role_remaining - allocated, available)
            if hours <= 0:
                continue
            assigned_by_bucket[bucket] = already_assigned + hours
            allocated += hours
            engineer_allocations.append(
                EngineerTaskAllocation(
                    engineer_id=engineer.engineer_id,
                    role=role,
                    hours=hours,
                    teams=engineer.teams,
                    task_team=task_team,
                    source_team_id=engineer.source_team_id,
                    source_bucket_capacity_hours=engineer.nominal_hours,
                )
            )
            if allocated >= role_remaining - 1e-9:
                break
        role_allocations[role] = allocated
    return AllocationOutcome(
        allocated_role_hours=role_allocations,
        engineer_allocations=tuple(engineer_allocations),
    )
