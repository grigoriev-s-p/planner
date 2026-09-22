"""Single-task feasibility checks over normalized planning data."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.skills import canonicalize_skill

from .capacity import calculate_engineer_capacity, calculate_team_capacity
from .availability import effective_capacity_hours
from .dependency_rules import (
    dependency_is_satisfied,
    dependency_timing,
    incoming_dependencies,
)
from .models import (
    DependencyCheck,
    EngineerAvailability,
    FeasibilityReason,
    FeasibilityResult,
    RoleCapacityCheck,
    SprintState,
    TeamCapacityCheck,
)
from .reasons import ReasonCode


TASK_SKILL_LIMITATION = (
    "The current processed dataset has no task-to-required-technology mapping; "
    "skill constraints are checked only when task_skill_requirements are supplied."
)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _text(value: Any) -> str | None:
    return None if _is_missing(value) else str(value).strip()


def _number(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}


@dataclass(frozen=True)
class FeasibilityContext:
    tasks: tuple[dict[str, Any], ...]
    task_role_effort: tuple[dict[str, Any], ...]
    dependencies: tuple[dict[str, Any], ...]
    engineers: tuple[dict[str, Any], ...]
    engineer_skills: tuple[dict[str, Any], ...]
    team_history: tuple[dict[str, Any], ...]
    task_skill_requirements: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_records(
        cls,
        *,
        tasks: Iterable[Mapping[str, Any]],
        task_role_effort: Iterable[Mapping[str, Any]],
        dependencies: Iterable[Mapping[str, Any]],
        engineers: Iterable[Mapping[str, Any]],
        engineer_skills: Iterable[Mapping[str, Any]],
        team_history: Iterable[Mapping[str, Any]],
        task_skill_requirements: Iterable[Mapping[str, Any]] = (),
    ) -> "FeasibilityContext":
        return cls(
            tasks=tuple(dict(row) for row in tasks),
            task_role_effort=tuple(dict(row) for row in task_role_effort),
            dependencies=tuple(dict(row) for row in dependencies),
            engineers=tuple(dict(row) for row in engineers),
            engineer_skills=tuple(dict(row) for row in engineer_skills),
            team_history=tuple(dict(row) for row in team_history),
            task_skill_requirements=tuple(dict(row) for row in task_skill_requirements),
        )


@dataclass
class _EngineerProfile:
    engineer_id: str
    team_id: str | None
    roles: set[str] = field(default_factory=set)
    capacity_rate: float = 0.0
    skills: set[str] = field(default_factory=set)


class FeasibilityService:
    """Reusable checker context so callers provide normalized records only once."""

    def __init__(self, context: FeasibilityContext):
        self.context = context
        self._tasks = {
            task_id: row
            for row in context.tasks
            if (task_id := _text(row.get("task_id"))) is not None
        }
        self._profiles = self._build_engineer_profiles()
        self._engineer_teams: dict[str, set[str | None]] = defaultdict(set)
        for profile in self._profiles.values():
            self._engineer_teams[profile.engineer_id].add(profile.team_id)

    def _build_engineer_profiles(self) -> dict[tuple[str, str | None], _EngineerProfile]:
        profiles: dict[tuple[str, str | None], _EngineerProfile] = {}
        for row in self.context.engineers:
            engineer_id = _text(row.get("engineer_id"))
            team = _text(row.get("team_id"))
            rate = _number(row.get("capacity_rate"))
            if engineer_id is None or rate is None or not 0 < rate <= 1:
                continue
            profile = profiles.setdefault(
                (engineer_id, team),
                _EngineerProfile(engineer_id=engineer_id, team_id=team),
            )
            role = _text(row.get("role"))
            if role:
                profile.roles.add(role)
            if profile.capacity_rate == 0:
                profile.capacity_rate = rate
            else:
                profile.capacity_rate = min(profile.capacity_rate, rate)
        for row in self.context.engineer_skills:
            engineer_id = _text(row.get("engineer_id"))
            technology = canonicalize_skill(_text(row.get("technology")))
            if engineer_id and technology:
                for profile in profiles.values():
                    if profile.engineer_id == engineer_id:
                        profile.skills.add(technology)
        return profiles

    def _bucket_used_hours(
        self,
        sprint_state: SprintState,
        task_team_id: str,
        availability_rates: Mapping[str, float] | None = None,
    ) -> dict[tuple[str, str], float]:
        """Return usage attributed to each engineer-team capacity bucket.

        Planner state always contains explicit bucket usage. For callers that
        only provide the legacy global usage, unattributed hours are consumed
        from the task team's bucket first and then from other buckets.
        """

        usage: dict[tuple[str, str], float] = {}
        profiles_by_engineer: dict[str, list[_EngineerProfile]] = defaultdict(list)
        for profile in self._profiles.values():
            profiles_by_engineer[profile.engineer_id].append(profile)
            usage[(profile.engineer_id, profile.team_id)] = float(
                sprint_state.engineer_team_hours_used
                .get(profile.engineer_id, {})
                .get(profile.team_id, 0.0)
            )

        for engineer_id, profiles in profiles_by_engineer.items():
            explicit_total = sum(
                usage[(engineer_id, profile.team_id)] for profile in profiles
            )
            unattributed = max(
                float(sprint_state.engineer_hours_used.get(engineer_id, 0.0))
                - explicit_total,
                0.0,
            )
            for profile in sorted(
                profiles,
                key=lambda item: (item.team_id != task_team_id, item.team_id),
            ):
                if unattributed <= 1e-9:
                    break
                key = (engineer_id, profile.team_id)
                capacity = calculate_engineer_capacity(
                    {
                        "engineer_id": engineer_id,
                        "capacity_rate": profile.capacity_rate,
                    }
                ).nominal_hours
                capacity = effective_capacity_hours(
                    capacity, (availability_rates or {}).get(engineer_id, 1.0)
                )
                consumed = min(max(capacity - usage[key], 0.0), unattributed)
                usage[key] += consumed
                unattributed -= consumed
        return usage

    def task_record(self, task_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        return dict(task) if task else None

    def task_role_requirements(self, task_id: str) -> dict[str, float]:
        requirements: dict[str, float] = defaultdict(float)
        for row in self.context.task_role_effort:
            if _text(row.get("task_id")) != task_id:
                continue
            role = _text(row.get("role"))
            hours = _number(row.get("required_hours"))
            if role and hours is not None and hours > 0:
                requirements[role] += hours
        return dict(sorted(requirements.items()))

    def task_skill_requirements(self, task_id: str) -> dict[str | None, set[str]]:
        requirements: dict[str | None, set[str]] = defaultdict(set)
        for row in self.context.task_skill_requirements:
            if _text(row.get("task_id")) != task_id:
                continue
            role = _text(row.get("role"))
            technology = canonicalize_skill(_text(row.get("technology")))
            if technology:
                requirements[role].add(technology)
        return requirements

    def incoming_task_dependencies(self, task_id: str) -> list[dict[str, Any]]:
        return incoming_dependencies(task_id, self.context.dependencies)

    def candidate_engineer_ids(self, task_id: str) -> list[str]:
        task = self._tasks.get(task_id, {})
        team_id = _text(task.get("team_id"))
        roles = set(self.task_role_requirements(task_id))
        if not team_id or not roles:
            return []
        candidate_ids = {
            profile.engineer_id
            for profile in self._profiles.values()
            if profile.roles.intersection(roles)
        }
        return sorted(
            candidate_ids,
            key=lambda engineer_id: (
                team_id not in self._engineer_teams[engineer_id],
                engineer_id,
            ),
        )

    def check(
        self,
        task_id: str,
        sprint_state: SprintState,
        *,
        role_hours_override: Mapping[str, float] | None = None,
        story_points_override: float | None = None,
        team_id_override: str | None = None,
        allow_cross_team: bool = True,
        unavailable_engineer_ids: set[str] | None = None,
        preferred_engineer_ids: set[str] | None = None,
        availability_rates: Mapping[str, float] | None = None,
    ) -> FeasibilityResult:
        reasons: list[FeasibilityReason] = []
        task = self._tasks.get(task_id)
        limitations = () if self.context.task_skill_requirements else (TASK_SKILL_LIMITATION,)
        if task is None:
            reasons.append(
                FeasibilityReason(
                    ReasonCode.UNKNOWN_TASK_DATA,
                    {"task_id": task_id, "message": "Task is absent from normalized tasks."},
                )
            )
            return FeasibilityResult(
                task_id, sprint_state.sprint_number, False, False, {}, None, (), (), (),
                tuple(reasons), limitations,
            )
        if _boolean(task.get("is_placeholder")):
            reasons.append(
                FeasibilityReason(
                    ReasonCode.UNKNOWN_TASK_DATA,
                    {
                        "task_id": task_id,
                        "message": (
                            "Task is a placeholder created because it is absent from the source "
                            "Tasks table."
                        ),
                    },
                )
            )
            return FeasibilityResult(
                task_id, sprint_state.sprint_number, False, False, dict(task), None, (), (), (),
                tuple(reasons), limitations,
            )

        team_id = _text(team_id_override) or _text(task.get("team_id"))
        source_story_points = _number(task.get("story_points"))
        story_points = (
            _number(story_points_override)
            if story_points_override is not None
            else source_story_points
        )
        missing_fields = [
            field
            for field, value in (("team_id", team_id), ("story_points", story_points))
            if value is None
        ]
        if missing_fields:
            reasons.append(
                FeasibilityReason(
                    ReasonCode.UNKNOWN_TASK_DATA,
                    {"task_id": task_id, "missing_fields": missing_fields},
                )
            )
            return FeasibilityResult(
                task_id, sprint_state.sprint_number, False, False, dict(task), None, (), (), (),
                tuple(reasons), limitations,
            )
        assert team_id is not None and story_points is not None

        nominal_team = calculate_team_capacity(team_id, self.context.team_history)
        used_sp = float(sprint_state.team_sp_used.get(team_id, 0.0))
        available_sp = max(nominal_team.capacity_sp - used_sp, 0.0)
        sp_deficit = max(story_points - available_sp, 0.0)
        team_check = TeamCapacityCheck(
            team_id=team_id,
            average_velocity_sp=nominal_team.average_velocity_sp,
            focus_factor=nominal_team.focus_factor,
            capacity_sp=nominal_team.capacity_sp,
            already_used_sp=used_sp,
            available_sp=available_sp,
            required_sp=story_points,
            deficit_sp=sp_deficit,
            result="OK" if sp_deficit == 0 else "FAIL",
            history_samples=nominal_team.history_samples,
        )
        if sp_deficit > 0:
            reasons.append(
                FeasibilityReason(
                    ReasonCode.INSUFFICIENT_TEAM_CAPACITY,
                    {
                        "team_id": team_id,
                        "required_sp": story_points,
                        "available_sp": available_sp,
                        "deficit_sp": sp_deficit,
                    },
                )
            )

        dependency_checks: list[DependencyCheck] = []
        dependencies_allow_start = True
        for dependency in self.incoming_task_dependencies(task_id):
            blocker = _text(dependency.get("blocking_task_id")) or ""
            satisfied, completed_sprint, earliest_start = dependency_is_satisfied(
                blocker,
                sprint_state.sprint_number,
                sprint_state.completed_tasks,
                sprint_state.actual_completed_tasks,
            )
            timing = dependency_timing(
                blocker,
                sprint_state.completed_tasks,
                actual_completed_tasks=sprint_state.actual_completed_tasks,
            )
            dependency_checks.append(
                DependencyCheck(
                    blocking_task_id=blocker,
                    blocked_task_id=task_id,
                    dependency_type=_text(dependency.get("dependency_type")),
                    completed_sprint=completed_sprint,
                    earliest_start_sprint=earliest_start,
                    completion_source=(
                        timing.completion_source.value if timing is not None else None
                    ),
                    result="OK" if satisfied else "FAIL",
                )
            )
            if not satisfied:
                dependencies_allow_start = False
                reasons.append(
                    FeasibilityReason(
                        ReasonCode.DEPENDENCY_NOT_COMPLETED,
                        {
                            "blocking_task_id": blocker,
                            "blocked_task_id": task_id,
                            "dependency_type": _text(dependency.get("dependency_type")),
                            "completed_sprint": completed_sprint,
                            "requested_sprint": sprint_state.sprint_number,
                            "earliest_start_sprint": earliest_start,
                            "completion_source": (
                                timing.completion_source.value if timing is not None else None
                            ),
                        },
                    )
                )
                if completed_sprint is None:
                    diagnostic_code = (
                        ReasonCode.MISSING_DEPENDENCY_REFERENCE
                        if blocker not in self._tasks
                        else ReasonCode.UNKNOWN_DEPENDENCY_DATE
                    )
                    reasons.append(
                        FeasibilityReason(
                            diagnostic_code,
                            {
                                "blocking_task_id": blocker,
                                "blocked_task_id": task_id,
                                "dependency_type": _text(dependency.get("dependency_type")),
                            },
                        )
                    )

        if role_hours_override is None:
            role_requirements = self.task_role_requirements(task_id)
        else:
            role_requirements = {
                str(role): float(hours)
                for role, hours in role_hours_override.items()
                if float(hours) > 0
            }
        skill_requirements = self.task_skill_requirements(task_id)
        task_required_skills = skill_requirements.get(None, set())
        role_checks: list[RoleCapacityCheck] = []
        engineer_rows: list[EngineerAvailability] = []
        any_role_work_possible = not role_requirements
        all_roles_complete = True
        availability_rates = availability_rates or {}
        bucket_used_hours = self._bucket_used_hours(
            sprint_state, team_id, availability_rates
        )
        shared_matching_buckets: dict[tuple[str, str], float] = {}
        task_skill_candidates: dict[str, set[str]] = defaultdict(set)

        for role, required_hours in role_requirements.items():
            required_skills = skill_requirements.get(role, set())
            # Organizers allow help across teams. Membership is a preference;
            # the canonical role, skills and personal hours remain constraints.
            unavailable = unavailable_engineer_ids or set()
            preferred = preferred_engineer_ids or set()
            role_profiles = sorted(
                (
                    profile
                    for profile in self._profiles.values()
                    if role in profile.roles
                    # A profile with no source team is a shared pool resource
                    # and is always eligible. Team-bound profiles still obey
                    # the caller's cross-team policy.
                    and (allow_cross_team or profile.team_id == team_id or profile.team_id is None)
                    and profile.engineer_id not in unavailable
                ),
                key=lambda profile: (
                    profile.engineer_id not in preferred,
                    profile.team_id != team_id,
                    -max(
                        effective_capacity_hours(
                            calculate_engineer_capacity(
                                {"engineer_id": profile.engineer_id,
                                 "capacity_rate": profile.capacity_rate}
                            ).nominal_hours,
                            availability_rates.get(profile.engineer_id, 1.0),
                        )
                        - bucket_used_hours.get(
                            (profile.engineer_id, profile.team_id), 0.0
                        ),
                        0.0,
                    ),
                    profile.engineer_id,
                    str(profile.team_id or ""),
                ),
            )
            matching_profiles = [
                profile for profile in role_profiles if required_skills.issubset(profile.skills)
            ]
            available_hours = 0.0
            nominal_hours = 0.0
            matching_ids: set[str] = set()
            for profile in role_profiles:
                capacity = calculate_engineer_capacity(
                    {
                        "engineer_id": profile.engineer_id,
                        "capacity_rate": profile.capacity_rate,
                    }
                )
                effective_hours = effective_capacity_hours(
                    capacity.nominal_hours,
                    availability_rates.get(profile.engineer_id, 1.0),
                )
                used_hours = bucket_used_hours.get(
                    (profile.engineer_id, profile.team_id), 0.0
                )
                remaining_hours = max(effective_hours - used_hours, 0.0)
                matches_skills = profile in matching_profiles
                engineer_rows.append(
                    EngineerAvailability(
                        engineer_id=profile.engineer_id,
                        source_team_id=profile.team_id,
                        teams=tuple(sorted(
                            (team for team in self._engineer_teams[profile.engineer_id] if team is not None),
                            key=str,
                        )),
                        roles=tuple(sorted(profile.roles)),
                        capacity_rate=profile.capacity_rate,
                        nominal_hours=effective_hours,
                        used_hours=used_hours,
                        available_hours=remaining_hours,
                        skills=tuple(sorted(profile.skills)),
                        required_role=role,
                        required_technologies=tuple(sorted(required_skills)),
                        matches_required_skills=matches_skills,
                    )
                )
                if matches_skills:
                    matching_ids.add(profile.engineer_id)
                    available_hours += remaining_hours
                    nominal_hours += effective_hours
                    key = (profile.engineer_id, profile.team_id)
                    shared_matching_buckets[key] = max(
                        shared_matching_buckets.get(key, 0.0), remaining_hours
                    )
                if matches_skills and remaining_hours > 0:
                    for technology in task_required_skills.intersection(profile.skills):
                        task_skill_candidates[technology].add(profile.engineer_id)

            deficit = max(required_hours - available_hours, 0.0)
            if available_hours > 0:
                any_role_work_possible = True
            if deficit > 0:
                all_roles_complete = False
                if required_skills:
                    reasons.append(
                        FeasibilityReason(
                            ReasonCode.INSUFFICIENT_SKILL_CAPACITY,
                            {
                                "role": role,
                                "required_technologies": sorted(required_skills),
                                "required_hours": required_hours,
                                "available_matching_hours": available_hours,
                                "deficit_hours": deficit,
                            },
                        )
                    )
                else:
                    reasons.append(
                        FeasibilityReason(
                            ReasonCode.INSUFFICIENT_ROLE_CAPACITY,
                            {
                                "role": role,
                                "required_hours": required_hours,
                                "available_hours": available_hours,
                                "deficit_hours": deficit,
                            },
                        )
                    )
                    if nominal_hours >= required_hours and available_hours < required_hours:
                        reasons.append(
                            FeasibilityReason(
                                ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY,
                                {
                                    "role": role,
                                    "required_hours": required_hours,
                                    "nominal_hours": nominal_hours,
                                    "available_hours": available_hours,
                                    "deficit_hours": deficit,
                                },
                            )
                        )
                if available_hours > 0:
                    reasons.append(
                        FeasibilityReason(
                            ReasonCode.MULTI_SPRINT_REQUIRED,
                            {"role": role, "remaining_hours": deficit},
                        )
                    )
            role_checks.append(
                RoleCapacityCheck(
                    role=role,
                    required_hours=required_hours,
                    available_hours=available_hours,
                    deficit_hours=deficit,
                    remaining_hours_after_sprint=deficit,
                    required_technologies=tuple(sorted(required_skills)),
                    matching_engineer_ids=tuple(sorted(matching_ids)),
                    result="OK" if deficit == 0 else "FAIL",
                )
            )

        unavailable_task_skills = sorted(
            technology
            for technology in task_required_skills
            if not task_skill_candidates.get(technology)
        )
        if unavailable_task_skills:
            external_skill_candidates = sorted({
                profile.engineer_id
                for profile in self._profiles.values()
                if profile.team_id != team_id
                and profile.engineer_id not in (unavailable_engineer_ids or set())
                and profile.roles.intersection(role_requirements)
                and set(unavailable_task_skills).intersection(profile.skills)
            })
            all_roles_complete = False
            reasons.append(
                FeasibilityReason(
                    ReasonCode.INSUFFICIENT_SKILL_CAPACITY,
                    {
                        "role": None,
                        "missing_skills": unavailable_task_skills,
                        "required_technologies": unavailable_task_skills,
                        "candidate_engineers": external_skill_candidates,
                        "cross_team_blocked": bool(external_skill_candidates and not allow_cross_team),
                    },
                )
            )

        # A profile may expose several roles, but its team capacity bucket can
        # only be spent once. Per-role totals alone would otherwise claim that
        # one 80-hour engineer can supply 80 hours for every listed role.
        total_required_role_hours = sum(role_requirements.values())
        total_shared_available = sum(shared_matching_buckets.values())
        shared_deficit = max(total_required_role_hours - total_shared_available, 0.0)
        if shared_deficit > 0 and all_roles_complete:
            all_roles_complete = False
            reasons.append(
                FeasibilityReason(
                    ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY,
                    {
                        "role": "MULTIPLE_ROLES",
                        "required_hours": total_required_role_hours,
                        "available_hours": total_shared_available,
                        "deficit_hours": shared_deficit,
                    },
                )
            )

        team_work_possible = story_points == 0 or available_sp > 0
        can_start = dependencies_allow_start and team_work_possible and any_role_work_possible
        can_complete = dependencies_allow_start and sp_deficit == 0 and all_roles_complete
        return FeasibilityResult(
            task_id=task_id,
            sprint=sprint_state.sprint_number,
            can_start=can_start,
            can_complete=can_complete,
            task=dict(task),
            team_capacity=team_check,
            role_capacity=tuple(role_checks),
            engineers=tuple(engineer_rows),
            dependencies=tuple(dependency_checks),
            reasons=tuple(reasons),
            limitations=limitations,
            task_required_technologies=tuple(sorted(task_required_skills)),
        )


def check_task_feasibility(
    task_id: str,
    sprint_number: int,
    sprint_state: SprintState,
    context: FeasibilityContext,
    *,
    role_hours_override: Mapping[str, float] | None = None,
    story_points_override: float | None = None,
    unavailable_engineer_ids: set[str] | None = None,
    preferred_engineer_ids: set[str] | None = None,
    availability_rates: Mapping[str, float] | None = None,
) -> FeasibilityResult:
    """Public convenience API for one feasibility check."""

    if sprint_number != sprint_state.sprint_number:
        raise ValueError("sprint_number must match sprint_state.sprint_number.")
    return FeasibilityService(context).check(
        task_id,
        sprint_state,
        role_hours_override=role_hours_override,
        story_points_override=story_points_override,
        unavailable_engineer_ids=unavailable_engineer_ids,
        preferred_engineer_ids=preferred_engineer_ids,
        availability_rates=availability_rates,
    )
