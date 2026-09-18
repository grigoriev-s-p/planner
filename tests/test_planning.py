from __future__ import annotations

import unittest

from src.planning.capacity import calculate_engineer_capacity, calculate_team_capacity
from src.planning.feasibility import FeasibilityContext, FeasibilityService
from src.planning.models import SprintState
from src.planning.reasons import ReasonCode


def context_for(
    *,
    tasks: list[dict] | None = None,
    effort: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    engineers: list[dict] | None = None,
    skills: list[dict] | None = None,
    skill_requirements: list[dict] | None = None,
    team_history: list[dict] | None = None,
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=tasks
        or [
            {
                "task_id": "T-1",
                "initiative_id": "PRODF-1",
                "team_id": "Team-A",
                "story_points": 5,
                "status": "ToDo",
                "is_placeholder": False,
            }
        ],
        task_role_effort=effort
        if effort is not None
        else [
            {"task_id": "T-1", "role": "Backend", "required_hours": 30},
            {"task_id": "T-1", "role": "QA", "required_hours": 20},
        ],
        dependencies=dependencies or [],
        engineers=engineers
        or [
            {
                "engineer_id": "E-BE",
                "team_id": "Team-A",
                "role": "Backend",
                "capacity_rate": 1.0,
            },
            {
                "engineer_id": "E-QA",
                "team_id": "Team-A",
                "role": "QA",
                "capacity_rate": 0.5,
            },
        ],
        engineer_skills=skills
        or [
            {"engineer_id": "E-BE", "technology": "Python"},
            {"engineer_id": "E-QA", "technology": "Pytest"},
        ],
        team_history=team_history
        or [
            {"team_id": "Team-A", "velocity_achieved": 10},
            {"team_id": "Team-A", "velocity_achieved": 20},
        ],
        task_skill_requirements=skill_requirements or [],
    )


def reason_codes(result) -> list[ReasonCode]:
    return [reason.code for reason in result.reasons]


class CapacityTests(unittest.TestCase):
    def test_full_time_engineer_has_80_hours(self) -> None:
        capacity = calculate_engineer_capacity({"engineer_id": "E-1", "capacity_rate": 1.0})
        self.assertEqual(capacity.nominal_hours, 80)

    def test_half_time_engineer_has_40_hours(self) -> None:
        capacity = calculate_engineer_capacity({"engineer_id": "E-1", "capacity_rate": 0.5})
        self.assertEqual(capacity.nominal_hours, 40)

    def test_historical_velocity_uses_mandatory_focus_factor(self) -> None:
        capacity = calculate_team_capacity(
            "Team-A",
            [
                {"team_id": "Team-A", "velocity_achieved": 10},
                {"team_id": "Team-A", "velocity_achieved": 20},
            ],
        )
        self.assertEqual(capacity.average_velocity_sp, 15)
        self.assertEqual(capacity.focus_factor, 0.8)
        self.assertEqual(capacity.capacity_sp, 12)


class FeasibilityTests(unittest.TestCase):
    def test_task_story_points_fit_remaining_capacity(self) -> None:
        result = FeasibilityService(context_for()).check(
            "T-1", SprintState(1, team_sp_used={"Team-A": 3})
        )
        self.assertTrue(result.can_complete)
        self.assertNotIn(ReasonCode.INSUFFICIENT_TEAM_CAPACITY, reason_codes(result))

    def test_task_story_points_do_not_fit_remaining_capacity(self) -> None:
        result = FeasibilityService(context_for()).check(
            "T-1", SprintState(1, team_sp_used={"Team-A": 10})
        )
        self.assertFalse(result.can_complete)
        self.assertIn(ReasonCode.INSUFFICIENT_TEAM_CAPACITY, reason_codes(result))
        self.assertEqual(result.team_capacity.deficit_sp, 3)

    def test_role_hours_are_sufficient(self) -> None:
        result = FeasibilityService(context_for()).check("T-1", SprintState(1))
        self.assertTrue(all(item.result == "OK" for item in result.role_capacity))
        self.assertNotIn(ReasonCode.INSUFFICIENT_ROLE_CAPACITY, reason_codes(result))

    def test_insufficient_role_hours_return_reason(self) -> None:
        context = context_for(
            effort=[{"task_id": "T-1", "role": "QA", "required_hours": 50}]
        )
        result = FeasibilityService(context).check("T-1", SprintState(1))
        self.assertIn(ReasonCode.INSUFFICIENT_ROLE_CAPACITY, reason_codes(result))
        self.assertEqual(result.role_capacity[0].available_hours, 40)
        self.assertEqual(result.role_capacity[0].deficit_hours, 10)

    def test_used_engineer_hours_reduce_availability(self) -> None:
        context = context_for(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 30}]
        )
        result = FeasibilityService(context).check(
            "T-1", SprintState(1, engineer_hours_used={"E-BE": 70})
        )
        self.assertEqual(result.role_capacity[0].available_hours, 10)
        self.assertIn(ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY, reason_codes(result))

    def test_part_time_engineer_keeps_separate_team_buckets(self) -> None:
        context = context_for(
            tasks=[
                {
                    "task_id": "T-1",
                    "team_id": "Team-B",
                    "story_points": 5,
                    "is_placeholder": False,
                }
            ],
            effort=[{"task_id": "T-1", "role": "QA", "required_hours": 60}],
            engineers=[
                {
                    "engineer_id": "E-SHARED",
                    "team_id": "Team-A",
                    "role": "QA",
                    "capacity_rate": 0.5,
                },
                {
                    "engineer_id": "E-SHARED",
                    "team_id": "Team-B",
                    "role": "QA",
                    "capacity_rate": 0.5,
                },
            ],
            skills=[{"engineer_id": "E-SHARED", "technology": "Pytest"}],
            team_history=[{"team_id": "Team-B", "velocity_achieved": 10}],
        )
        result = FeasibilityService(context).check(
            "T-1",
            SprintState(
                1,
                engineer_hours_used={"E-SHARED": 25},
                engineer_team_hours_used={"E-SHARED": {"Team-B": 25}},
            ),
        )
        self.assertEqual(result.role_capacity[0].available_hours, 55)
        self.assertFalse(result.can_complete)

    def test_role_without_required_skill_is_not_matching_capacity(self) -> None:
        context = context_for(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 20}],
            skill_requirements=[
                {"task_id": "T-1", "role": "Backend", "technology": "Go"}
            ],
        )
        result = FeasibilityService(context).check("T-1", SprintState(1))
        self.assertEqual(result.role_capacity[0].available_hours, 0)
        self.assertIn(ReasonCode.INSUFFICIENT_SKILL_CAPACITY, reason_codes(result))

    def test_placeholder_task_returns_unknown_task_data(self) -> None:
        context = context_for(
            tasks=[
                {
                    "task_id": "P-1",
                    "summary": "Unknown task",
                    "is_placeholder": True,
                }
            ],
            effort=[],
        )
        result = FeasibilityService(context).check("P-1", SprintState(1))
        self.assertFalse(result.can_start)
        self.assertFalse(result.can_complete)
        self.assertEqual(reason_codes(result), [ReasonCode.UNKNOWN_TASK_DATA])

    def test_planned_blocker_keeps_one_full_gap_sprint(self) -> None:
        context = context_for(
            dependencies=[
                {
                    "blocking_task_id": "BLOCK-1",
                    "blocked_task_id": "T-1",
                    "dependency_type": "depends on",
                }
            ]
        )
        blocked_in_gap = FeasibilityService(context).check(
            "T-1", SprintState(2, completed_tasks={"BLOCK-1": 1})
        )
        self.assertFalse(blocked_in_gap.can_start)
        self.assertEqual(blocked_in_gap.dependencies[0].earliest_start_sprint, 3)
        self.assertEqual(blocked_in_gap.dependencies[0].completion_source, "PLANNED")

        allowed = FeasibilityService(context).check(
            "T-1", SprintState(3, completed_tasks={"BLOCK-1": 1})
        )
        self.assertTrue(allowed.can_start)
        self.assertEqual(allowed.dependencies[0].result, "OK")

    def test_actual_blocker_completion_unlocks_next_sprint(self) -> None:
        context = context_for(
            dependencies=[
                {
                    "blocking_task_id": "BLOCK-1",
                    "blocked_task_id": "T-1",
                    "dependency_type": "depends on",
                }
            ]
        )
        result = FeasibilityService(context).check(
            "T-1",
            SprintState(2, actual_completed_tasks={"BLOCK-1": 1}),
        )
        self.assertTrue(result.can_start)
        self.assertEqual(result.dependencies[0].earliest_start_sprint, 2)
        self.assertEqual(result.dependencies[0].completion_source, "ACTUAL")

    def test_unfinished_blocker_prevents_start(self) -> None:
        context = context_for(
            dependencies=[
                {"blocking_task_id": "BLOCK-1", "blocked_task_id": "T-1", "dependency_type": "depends on"}
            ]
        )
        result = FeasibilityService(context).check("T-1", SprintState(2))
        self.assertFalse(result.can_start)
        self.assertIn(ReasonCode.DEPENDENCY_NOT_COMPLETED, reason_codes(result))
        self.assertIn(ReasonCode.MISSING_DEPENDENCY_REFERENCE, reason_codes(result))

    def test_blocker_completed_in_same_sprint_does_not_allow_start(self) -> None:
        context = context_for(
            dependencies=[
                {"blocking_task_id": "BLOCK-1", "blocked_task_id": "T-1", "dependency_type": "is required for"}
            ]
        )
        result = FeasibilityService(context).check(
            "T-1", SprintState(2, completed_tasks={"BLOCK-1": 2})
        )
        self.assertFalse(result.can_start)
        self.assertEqual(result.dependencies[0].earliest_start_sprint, 4)

    def test_dependency_type_never_reverses_raw_columns(self) -> None:
        context = context_for(
            dependencies=[
                {"blocking_task_id": "T-1", "blocked_task_id": "OTHER", "dependency_type": "depends on"}
            ]
        )
        result = FeasibilityService(context).check("T-1", SprintState(1))
        self.assertEqual(result.dependencies, ())

    def test_large_role_effort_can_start_but_not_complete(self) -> None:
        context = context_for(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 130}]
        )
        result = FeasibilityService(context).check("T-1", SprintState(1))
        self.assertTrue(result.can_start)
        self.assertFalse(result.can_complete)
        self.assertEqual(result.role_capacity[0].remaining_hours_after_sprint, 50)
        self.assertIn(ReasonCode.MULTI_SPRINT_REQUIRED, reason_codes(result))

    def test_multiple_constraint_reasons_are_returned_together(self) -> None:
        context = context_for(
            tasks=[
                {
                    "task_id": "T-1",
                    "team_id": "Team-A",
                    "story_points": 20,
                    "is_placeholder": False,
                }
            ],
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 130}],
            dependencies=[
                {"blocking_task_id": "BLOCK-1", "blocked_task_id": "T-1", "dependency_type": "has to be done before"}
            ],
        )
        result = FeasibilityService(context).check("T-1", SprintState(1))
        codes = set(reason_codes(result))
        self.assertIn(ReasonCode.INSUFFICIENT_TEAM_CAPACITY, codes)
        self.assertIn(ReasonCode.INSUFFICIENT_ROLE_CAPACITY, codes)
        self.assertIn(ReasonCode.DEPENDENCY_NOT_COMPLETED, codes)
        self.assertIn(ReasonCode.MULTI_SPRINT_REQUIRED, codes)


if __name__ == "__main__":
    unittest.main()
