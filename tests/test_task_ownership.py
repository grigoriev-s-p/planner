from __future__ import annotations

import unittest

from src.planning import FeasibilityContext, PlanStatus, build_quarter_plan
from src.planning.allocation import allocate_available_hours
from src.planning.feasibility import FeasibilityService
from src.planning.models import SprintState
from src.planning.ownership import evaluate_task_ownership
from src.planning.plan_models import EngineerTaskAllocation, TaskAllocation
from src.web.constructor import ConstructorScenario, build_constructor_plan
from src.web.recommendations import RecommendationDraft


def engineer(engineer_id: str, team: str, rate: float, role: str = "Backend") -> dict:
    return {
        "engineer_id": engineer_id,
        "team_id": team,
        "capacity_rate": rate,
        "role": role,
    }


def context(
    engineers: list[dict],
    *,
    hours: float = 80,
    story_points: float = 2,
    team_history: list[dict] | None = None,
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=[{
            "task_id": "TASK", "team_id": "Team-A", "rung": 100,
            "story_points": story_points, "status": "ToDo",
            "is_placeholder": False,
        }],
        task_role_effort=[{
            "task_id": "TASK", "role": "Backend", "required_hours": hours,
        }],
        dependencies=[],
        engineers=engineers,
        engineer_skills=[],
        team_history=team_history or [
            {"team_id": "Team-A", "velocity_achieved": 20},
            {"team_id": "Team-B", "velocity_achieved": 20},
        ],
    )


def task_allocation(
    owner_team: str,
    local: list[tuple[str, float]],
    external: list[tuple[str, float]],
    *,
    sprint: int = 1,
) -> TaskAllocation:
    rows = [
        EngineerTaskAllocation(
            engineer_id=engineer_id,
            role="Backend",
            hours=hours,
            teams=(owner_team,),
            task_team=owner_team,
            source_team_id=owner_team,
            source_bucket_capacity_hours=80,
        )
        for engineer_id, hours in local
    ]
    rows.extend(
        EngineerTaskAllocation(
            engineer_id=engineer_id,
            role="Backend",
            hours=hours,
            teams=("Team-X",),
            task_team=owner_team,
            source_team_id="Team-X",
            source_bucket_capacity_hours=80,
        )
        for engineer_id, hours in external
    )
    total = sum(item.hours for item in rows)
    return TaskAllocation(
        task_id="TASK",
        sprint_number=sprint,
        allocated_role_hours={"Backend": total},
        engineer_allocations=tuple(rows),
        task_story_points=2,
        capacity_sp_load=2,
        planned_completion_sp=2,
        completed_sp=0,
        is_started=True,
        is_completed=True,
    )


def constructor_payload() -> dict:
    return {
        "teams": [
            {"team_id": "Team-A", "name": "A", "expected_velocity_sp": 20, "focus_factor": 0.8},
            {"team_id": "Team-L", "name": "L", "expected_velocity_sp": 20, "focus_factor": 0.8},
        ],
        "engineers": [
            {
                "engineer_id": "ENG-A", "name": "Alpha Engineer",
                "assignments": [{"team_id": "Team-A", "role": "Backend", "capacity_rate": 1}],
                "skills": [],
            },
            {
                "engineer_id": "ENG-L", "name": "Legacy Engineer",
                "assignments": [{"team_id": "Team-L", "role": "Backend", "capacity_rate": 1}],
                "skills": [],
            },
        ],
        "tasks": [{
            "task_id": "TASK", "title": "Task", "initiative_id": "I-1",
            "team_id": "Team-L", "status": "ToDo", "rung": 100,
            "story_points": 2,
            "role_requirements": [{"role": "Backend", "required_hours": 40, "skills": []}],
            "depends_on": [],
        }],
    }


class TaskOwnershipTests(unittest.TestCase):
    def test_own_team_first_uses_sixty_local_then_twenty_external(self) -> None:
        data = context([
            engineer("A1", "Team-A", 0.5),
            engineer("A2", "Team-A", 0.25),
            engineer("B1", "Team-B", 0.5),
        ])
        check = FeasibilityService(data).check("TASK", SprintState(1))
        outcome = allocate_available_hours({"Backend": 80}, check)
        self.assertEqual(
            [(row.engineer_id, row.hours, row.cross_team) for row in outcome.engineer_allocations],
            [("A1", 40, False), ("A2", 20, False), ("B1", 20, True)],
        )

    def test_external_does_not_replace_available_local_engineer(self) -> None:
        data = context([
            engineer("A1", "Team-A", 0.5),
            engineer("B1", "Team-B", 0.5),
        ], hours=30)
        check = FeasibilityService(data).check("TASK", SprintState(1))
        outcome = allocate_available_hours({"Backend": 30}, check)
        self.assertEqual(
            [(row.engineer_id, row.hours) for row in outcome.engineer_allocations],
            [("A1", 30)],
        )

    def test_external_engineers_do_not_bypass_owner_sp_limit(self) -> None:
        data = context(
            [
                engineer("A1", "Team-A", 0.5),
                engineer("B1", "Team-B", 1),
            ],
            hours=40,
            story_points=10,
            team_history=[
                {"team_id": "Team-A", "velocity_achieved": 1.25},
                {"team_id": "Team-B", "velocity_achieved": 100},
            ],
        )
        plan = build_quarter_plan(data, allow_team_reassignment=False)
        result = plan.task_results[0]
        self.assertEqual(result.status, PlanStatus.MOVED)
        self.assertTrue(all(sprint.team_sp_used.get("Team-B", 0) == 0 for sprint in plan.sprints))

    def test_valid_reassignment_ownership(self) -> None:
        evidence = evaluate_task_ownership(
            "Team-A",
            [task_allocation(
                "Team-A",
                [("A1", 30), ("A2", 30), ("A3", 20)],
                [("X1", 20), ("X2", 20)],
            )],
        )
        self.assertTrue(evidence.valid)
        self.assertAlmostEqual(evidence.owner_share or 0, 2 / 3)

    def test_invalid_owner_assignment_by_hours(self) -> None:
        evidence = evaluate_task_ownership(
            "Team-A",
            [task_allocation("Team-A", [("A1", 20)], [("X1", 50), ("X2", 50)])],
        )
        self.assertFalse(evidence.valid)
        self.assertFalse(evidence.hours_valid)

    def test_invalid_owner_assignment_by_headcount(self) -> None:
        evidence = evaluate_task_ownership(
            "Team-A",
            [task_allocation(
                "Team-A",
                [("A1", 40), ("A2", 40)],
                [("X1", 10), ("X2", 10), ("X3", 10)],
            )],
        )
        self.assertFalse(evidence.valid)
        self.assertFalse(evidence.headcount_valid)

    def test_headcount_tie_is_allowed_with_owner_hours_majority(self) -> None:
        evidence = evaluate_task_ownership(
            "Team-A",
            [task_allocation(
                "Team-A",
                [("A1", 40), ("A2", 40)],
                [("X1", 20), ("X2", 20)],
            )],
        )
        self.assertTrue(evidence.valid)

    def test_external_only_active_sprint_is_invalid(self) -> None:
        evidence = evaluate_task_ownership(
            "Team-A",
            [
                task_allocation("Team-A", [("A1", 80)], [], sprint=1),
                task_allocation("Team-A", [], [("X1", 20)], sprint=2),
            ],
        )
        self.assertFalse(evidence.valid)
        self.assertFalse(evidence.sprint_participation_valid)

    def test_small_task_does_not_force_all_local_engineers_to_participate(self) -> None:
        data = context([
            engineer("A1", "Team-A", 0.5),
            engineer("A2", "Team-A", 0.5),
            engineer("A3", "Team-A", 0.25),
            engineer("B1", "Team-B", 1),
        ], hours=8)
        check = FeasibilityService(data).check("TASK", SprintState(1))
        outcome = allocate_available_hours({"Backend": 8}, check)
        self.assertEqual(len(outcome.engineer_allocations), 1)
        self.assertFalse(outcome.engineer_allocations[0].cross_team)

    def test_reassignment_rebuilds_allocation_for_new_owner(self) -> None:
        base = build_constructor_plan(ConstructorScenario(**constructor_payload()))
        draft = RecommendationDraft(base)
        reassigned = draft._plan([{
            "type": "TASK_REASSIGNMENT",
            "task_id": "TASK",
            "team_id": "Team-A",
            "source_team_id": "Team-L",
        }])
        task = reassigned["tasks"][0]
        self.assertEqual(task["assigned_team_id"], "Team-A")
        self.assertEqual({row["engineer_id"] for row in task["allocations"]}, {"ENG-A"})
        self.assertNotIn("ENG-L", {row["engineer_id"] for row in task["allocations"]})

    def test_swap_rebuilds_both_task_allocations(self) -> None:
        payload = constructor_payload()
        payload["tasks"].append({
            **payload["tasks"][0],
            "task_id": "TASK-2",
            "initiative_id": "I-2",
            "team_id": "Team-A",
            "rung": 90,
        })
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**payload))
        )
        swapped = draft._plan([{
            "type": "TASK_SWAP",
            "changes": [
                {"type": "TASK_REASSIGNMENT", "task_id": "TASK", "team_id": "Team-A", "source_team_id": "Team-L"},
                {"type": "TASK_REASSIGNMENT", "task_id": "TASK-2", "team_id": "Team-L", "source_team_id": "Team-A"},
            ],
        }])
        by_id = {task["task_id"]: task for task in swapped["tasks"]}
        self.assertEqual({row["engineer_id"] for row in by_id["TASK"]["allocations"]}, {"ENG-A"})
        self.assertEqual({row["engineer_id"] for row in by_id["TASK-2"]["allocations"]}, {"ENG-L"})

    def test_best_plan_satisfies_ownership_constraints(self) -> None:
        payload = constructor_payload()
        payload["tasks"][0]["team_id"] = "Team-A"
        payload["tasks"][0]["role_requirements"][0]["required_hours"] = 400
        payload["engineers"][0]["assignments"][0]["capacity_rate"] = 0.75
        payload["engineers"][1]["assignments"][0]["capacity_rate"] = 0.25
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**payload))
        )
        task = draft.optimized_plan["tasks"][0]
        self.assertEqual(task["status"], "PLANNED")
        self.assertTrue(task["ownership"]["valid"])
        self.assertGreater(task["ownership"]["owner_hours"], task["ownership"]["external_hours"])
        self.assertGreaterEqual(
            task["ownership"]["owner_engineer_count"],
            task["ownership"]["external_engineer_count"],
        )

    def test_screenshot_like_external_majority_is_not_recommended(self) -> None:
        before = {"tasks": [{
            "task_id": "TASK", "status": "MOVED", "assigned_team_id": "Team-A",
            "rung": 100, "allocations": [],
        }]}
        rows = [{
            "engineer_id": "A1", "role": "Backend", "source_team_id": "Team-A",
            "assigned_task_team_id": "Team-A", "sprint": 1,
            "allocated_hours": 20, "cross_team": False,
        }]
        rows.extend({
            "engineer_id": f"X{index}", "role": "Backend", "source_team_id": "Team-X",
            "assigned_task_team_id": "Team-A", "sprint": 1,
            "allocated_hours": 20, "cross_team": True,
        } for index in range(1, 6))
        after = {"tasks": [{
            "task_id": "TASK", "status": "PLANNED", "assigned_team_id": "Team-A",
            "completion_sprint": 1, "rung": 100, "allocations": rows,
        }]}
        draft = RecommendationDraft(before)
        self.assertIsNone(draft._recommendation(
            {"type": "CROSS_TEAM_HELP", "task_id": "TASK"}, before, after
        ))


if __name__ == "__main__":
    unittest.main()
