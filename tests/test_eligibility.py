from __future__ import annotations

import unittest

from src.planning import (
    BaselineQuarterPlanner,
    EligibilityContext,
    EligibilityService,
    FeasibilityContext,
    PlanStatus,
    build_quarter_plan,
)
from src.planning.reasons import ReasonCode


def eligibility_service(*, effort: list[dict], engineers: list[dict]) -> EligibilityService:
    return EligibilityService(
        EligibilityContext.from_records(
            task_role_effort=effort,
            engineers=engineers,
        )
    )


def planning_context(
    tasks: list[dict], *, effort: list[dict], engineers: list[dict]
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=tasks,
        task_role_effort=effort,
        dependencies=[],
        engineers=engineers,
        engineer_skills=[],
        team_history=[{"team_id": "Team-A", "velocity_achieved": 20}],
    )


def task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "initiative_id": "PRODF-1",
        "team_id": "Team-A",
        "story_points": 5,
        "status": "ToDo",
        "rung": 1,
        "is_placeholder": False,
    }


class EligibilityTests(unittest.TestCase):
    def test_task_with_existing_role_is_eligible(self) -> None:
        service = eligibility_service(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 80}],
            engineers=[{"engineer_id": "E-1", "role": "Backend"}],
        )
        result = service.check(task("T-1"))
        self.assertTrue(result.eligible)
        self.assertEqual(result.reasons, ())

    def test_task_with_missing_role_is_ineligible(self) -> None:
        service = eligibility_service(
            effort=[
                {"task_id": "T-1", "role": "Разработчик 1С", "required_hours": 80}
            ],
            engineers=[{"engineer_id": "E-1", "role": "Backend"}],
        )
        result = service.check(task("T-1"))
        self.assertFalse(result.eligible)
        self.assertEqual(result.missing_roles, ("Разработчик 1С",))
        self.assertEqual(result.reasons[0].code, ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST)
        self.assertEqual(result.reasons[0].required_hours, 80)
        self.assertEqual(result.reasons[0].available_specialists, 0)

    def test_confirmed_role_aliases_match_after_normalization(self) -> None:
        service = eligibility_service(
            effort=[
                {"task_id": "T-1", "role": "Разработчик IOS", "required_hours": 40}
            ],
            engineers=[{"engineer_id": "E-1", "role": "Разработчик iOS"}],
        )
        self.assertTrue(service.check(task("T-1")).eligible)

    def test_ineligible_task_is_excluded_from_planner_candidates(self) -> None:
        tasks = [task("MISSING"), task("READY")]
        context = planning_context(
            tasks,
            effort=[
                {"task_id": "MISSING", "role": "Разработчик 1С", "required_hours": 80},
                {"task_id": "READY", "role": "Backend", "required_hours": 20},
            ],
            engineers=[
                {
                    "engineer_id": "E-1",
                    "team_id": "Team-A",
                    "role": "Backend",
                    "capacity_rate": 1,
                }
            ],
        )
        planner = BaselineQuarterPlanner(context)
        self.assertNotIn("MISSING", planner.scoring_context.eligible_task_ids)
        self.assertIn("READY", planner.scoring_context.eligible_task_ids)

        plan = planner.plan()
        missing = next(item for item in plan.task_results if item.task_id == "MISSING")
        ready = next(item for item in plan.task_results if item.task_id == "READY")
        self.assertEqual(missing.status, PlanStatus.INELIGIBLE_DATA_CONSTRAINT)
        self.assertEqual(missing.priority_order, None)
        self.assertEqual(missing.placement_attempts, ())
        self.assertEqual(ready.status, PlanStatus.PLANNED)

    def test_insufficient_hours_with_existing_role_is_still_eligible(self) -> None:
        service = eligibility_service(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 120}],
            engineers=[
                {
                    "engineer_id": "E-1",
                    "role": "Backend",
                    "capacity_rate": 0.5,
                }
            ],
        )
        self.assertTrue(service.check(task("T-1")).eligible)

    def test_multi_sprint_task_with_existing_role_is_still_eligible(self) -> None:
        service = eligibility_service(
            effort=[{"task_id": "T-1", "role": "Backend", "required_hours": 500}],
            engineers=[{"engineer_id": "E-1", "role": "Backend"}],
        )
        self.assertTrue(service.check(task("T-1")).eligible)

    def test_ineligible_task_is_preserved_in_plan_report(self) -> None:
        context = planning_context(
            [task("MISSING")],
            effort=[
                {"task_id": "MISSING", "role": "Разработчик 1С", "required_hours": 80}
            ],
            engineers=[
                {
                    "engineer_id": "E-1",
                    "team_id": "Team-A",
                    "role": "Backend",
                    "capacity_rate": 1,
                }
            ],
        )
        plan = build_quarter_plan(context)
        self.assertEqual(len(plan.ineligible_tasks), 1)
        self.assertEqual(plan.ineligible_tasks[0].task_id, "MISSING")
        self.assertEqual(plan.summary()["blocked_by_missing_specialists"], 1)
        serialized = plan.to_dict()["task_results"][0]
        self.assertFalse(serialized["eligibility"]["eligible"])
        self.assertEqual(
            serialized["eligibility"]["reasons"][0]["code"],
            "NO_AVAILABLE_ROLE_SPECIALIST",
        )


if __name__ == "__main__":
    unittest.main()
