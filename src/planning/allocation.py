"""Deterministic greedy allocation of remaining role hours to engineers."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .models import FeasibilityResult
from .ownership import TaskOwnership
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
    prior_ownership: TaskOwnership | None = None,
    enforce_owner_majority: bool = False,
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
    outcome = AllocationOutcome(
        allocated_role_hours=role_allocations,
        engineer_allocations=tuple(engineer_allocations),
    )
    if not enforce_owner_majority or task_team is None:
        return outcome
    return _limit_cross_team_to_owner(
        outcome,
        remaining_role_hours=remaining_role_hours,
        task_team=task_team,
        prior_ownership=prior_ownership,
    )


def _limit_cross_team_to_owner(
    outcome: AllocationOutcome,
    *,
    remaining_role_hours: dict[str, float],
    task_team: str,
    prior_ownership: TaskOwnership | None,
) -> AllocationOutcome:
    """Keep external help residual and bounded by actual owner participation.

    The greedy allocator still consumes every matching local bucket first. This
    second, cheap pass only limits external rows so a task cannot finish with
    an external-hours majority or more external than owner-team performers.
    A temporary hours tie may continue into a later sprint; a final tie is
    deliberately deferred so later owner capacity can perform the remainder.
    """

    previous_owner_hours = prior_ownership.owner_hours if prior_ownership else 0.0
    previous_external_hours = (
        prior_ownership.external_hours if prior_ownership else 0.0
    )
    owner_ids = set(prior_ownership.owner_engineer_ids if prior_ownership else ())
    external_ids = set(
        prior_ownership.external_engineer_ids if prior_ownership else ()
    )
    local_rows = [
        row for row in outcome.engineer_allocations
        if row.source_team_id == task_team and row.hours > 1e-9
    ]
    external_rows = [
        row for row in outcome.engineer_allocations
        if row.source_team_id != task_team and row.hours > 1e-9
    ]
    owner_ids.update(row.engineer_id for row in local_rows)
    local_hours_this_sprint = sum(row.hours for row in local_rows)
    owner_hours = previous_owner_hours + local_hours_this_sprint
    external_hours = previous_external_hours
    accepted_external: list[EngineerTaskAllocation] = []

    # External-only sprints are not allowed. Headcount is checked on unique
    # actual performers, never on all catalog members.
    if local_hours_this_sprint > 1e-9:
        for row in external_rows:
            is_new = row.engineer_id not in external_ids
            if is_new and len(external_ids) >= len(owner_ids):
                continue
            available_for_external = max(owner_hours - external_hours, 0.0)
            hours = min(row.hours, available_for_external)
            if hours <= 1e-9:
                continue
            accepted_external.append(
                row if abs(hours - row.hours) <= 1e-9 else replace(row, hours=hours)
            )
            external_hours += hours
            external_ids.add(row.engineer_id)

    accepted = [*local_rows, *accepted_external]
    role_allocations: dict[str, float] = {}
    for row in accepted:
        role_allocations[row.role] = role_allocations.get(row.role, 0.0) + row.hours
    for role in remaining_role_hours:
        role_allocations.setdefault(role, 0.0)

    completes_task = all(
        role_allocations.get(role, 0.0) >= max(float(hours), 0.0) - 1e-9
        for role, hours in remaining_role_hours.items()
    )
    if (
        completes_task
        and accepted_external
        and external_hours >= owner_hours - 1e-9
    ):
        # Avoid a completed 50/50 task. Removing the last external contribution
        # leaves real residual work for a later owner-first sprint.
        accepted.remove(accepted_external[-1])
        role = accepted_external[-1].role
        role_allocations[role] = max(
            role_allocations.get(role, 0.0) - accepted_external[-1].hours,
            0.0,
        )

    return AllocationOutcome(
        allocated_role_hours=role_allocations,
        engineer_allocations=tuple(accepted),
    )
