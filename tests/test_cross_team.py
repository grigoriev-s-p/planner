from __future__ import annotations

import unittest
from dataclasses import replace

from src.planning import FeasibilityContext, PlanStatus, build_quarter_plan
from src.planning.allocation import allocate_available_hours
from src.planning.eligibility import EligibilityContext, EligibilityService
from src.planning.feasibility import FeasibilityService
from src.planning.models import SprintState
from src.planning.plan_models import PlacementOutcome
from src.planning.reasons import ReasonCode


def task(task_id: str = "T", team: str = "Team-A", rung: float = 80) -> dict:
    return {
        "task_id": task_id, "team_id": team, "rung": rung,
        "story_points": 2, "status": "ToDo", "is_placeholder": False,
    }


def engineer(engineer_id: str, team: str, rate: float = 1, role: str = "Backend") -> dict:
    return {
        "engineer_id": engineer_id, "team_id": team,
        "capacity_rate": rate, "role": role,
    }


def context(
    engineers: list[dict],
    *,
    hours: float = 40,
    role: str = "Backend",
    tasks: list[dict] | None = None,
    efforts: list[dict] | None = None,
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=tasks if tasks is not None else [task()],
        task_role_effort=efforts if efforts is not None else [
            {"task_id": "T", "role": role, "required_hours": hours}
        ],
        engineers=engineers,
        dependencies=[],
        engineer_skills=[],
        team_history=[
            {"team_id": team, "velocity_achieved": 10}
            for team in ("Team-A", "Team-B", "Team-C")
        ],
    )


def result(plan, task_id: str = "T"):
    return next(item for item in plan.task_results if item.task_id == task_id)


class CrossTeamTests(unittest.TestCase):
    def test_local_engineer_is_preferred_even_with_less_free_time(self) -> None:
        data = context([engineer("Z-LOCAL", "Team-A"), engineer("A-EXT", "Team-B")])
        check = FeasibilityService(data).check(
            "T", SprintState(1, engineer_hours_used={"Z-LOCAL": 20})
        )
        allocation = allocate_available_hours({"Backend": 40}, check)
        self.assertTrue(check.can_complete)
        self.assertEqual(
            [(item.engineer_id, item.hours, item.cross_team) for item in allocation.engineer_allocations],
            [("Z-LOCAL", 40, False)],
        )

    def test_local_shortage_uses_sixty_local_and_forty_external_hours(self) -> None:
        data = context(
            [engineer("LOCAL", "Team-A"), engineer("EXT", "Team-B")], hours=100
        )
        check = FeasibilityService(data).check(
            "T", SprintState(1, engineer_hours_used={"LOCAL": 20})
        )
        allocation = allocate_available_hours({"Backend": 100}, check)
        self.assertTrue(check.can_complete)
        self.assertEqual(check.role_capacity[0].available_hours, 140)
        self.assertEqual(
            [(item.engineer_id, item.hours) for item in allocation.engineer_allocations],
            [("LOCAL", 60), ("EXT", 40)],
        )

    def test_role_available_only_in_other_team_passes_eligibility_and_feasibility(self) -> None:
        data = context([engineer("EXT", "Team-B", role="Java")], role="Java")
        eligibility = EligibilityService(EligibilityContext.from_records(
            task_role_effort=data.task_role_effort, engineers=data.engineers,
        ))
        service = FeasibilityService(data)
        self.assertTrue(eligibility.check(data.tasks[0]).eligible)
        self.assertEqual(service.candidate_engineer_ids("T"), ["EXT"])
        check = service.check("T", SprintState(1))
        self.assertTrue(check.can_complete)
        self.assertEqual(check.role_capacity[0].available_hours, 80)
        self.assertEqual(result(build_quarter_plan(data)).status, PlanStatus.PLANNED)

    def test_external_engineer_shares_eighty_hours_across_task_teams(self) -> None:
        data = context(
            [engineer("EXT", "Team-C")],
            tasks=[task("A", "Team-A", 100), task("B", "Team-B", 50)],
            efforts=[
                {"task_id": name, "role": "Backend", "required_hours": 50}
                for name in ("A", "B")
            ],
        )
        plan = build_quarter_plan(data)
        self.assertEqual(plan.sprints[0].engineer_hours_used["EXT"], 80)
        self.assertEqual(plan.sprints[1].engineer_hours_used["EXT"], 20)
        self.assertEqual(result(plan, "A").completion_sprint, 1)
        self.assertEqual(result(plan, "B").completion_sprint, 2)
        self.assertTrue(all(s.engineer_hours_used.get("EXT", 0) <= 80 for s in plan.sprints))

    def test_part_time_profiles_in_two_teams_have_two_forty_hour_buckets(self) -> None:
        data = context(
            [engineer("EXT", "Team-B", 0.5), engineer("EXT", "Team-C", 0.5)],
            hours=60,
        )
        check = FeasibilityService(data).check("T", SprintState(1))
        self.assertEqual(check.role_capacity[0].available_hours, 80)
        self.assertEqual(len(check.engineers), 2)
        self.assertEqual(
            {item.source_team_id: item.available_hours for item in check.engineers},
            {"Team-B": 40, "Team-C": 40},
        )
        plan = build_quarter_plan(data)
        self.assertEqual(result(plan).completion_sprint, 1)
        self.assertEqual(plan.sprints[0].engineer_hours_used["EXT"], 60)
        self.assertEqual(
            plan.sprints[0].engineer_team_hours_used["EXT"],
            {"Team-B": 40, "Team-C": 20},
        )

    def test_local_bucket_is_used_before_cross_team_bucket(self) -> None:
        data = context(
            [engineer("E-1", "Team-A", 0.5), engineer("E-1", "Team-B", 0.5)],
            hours=60,
        )
        check = FeasibilityService(data).check("T", SprintState(1))
        allocation = allocate_available_hours({"Backend": 60}, check)

        self.assertTrue(check.can_complete)
        self.assertEqual(check.role_capacity[0].available_hours, 80)
        self.assertEqual(
            [
                (item.source_team_id, item.hours, item.cross_team)
                for item in allocation.engineer_allocations
            ],
            [("Team-A", 40, False), ("Team-B", 20, True)],
        )
        external = allocation.engineer_allocations[1]
        self.assertEqual(external.assigned_task_team_id, "Team-A")
        self.assertEqual(external.source_bucket_capacity_hours, 40)
        self.assertEqual(external.capacity_share, 0.5)

    def test_cross_team_bucket_remainder_is_available_to_its_team(self) -> None:
        data = context(
            [engineer("E-1", "Team-A", 0.5), engineer("E-1", "Team-B", 0.5)],
            hours=21,
            tasks=[task("T", "Team-B")],
        )
        state = SprintState(
            1,
            engineer_hours_used={"E-1": 60},
            engineer_team_hours_used={"E-1": {"Team-A": 40, "Team-B": 20}},
        )
        check = FeasibilityService(data).check("T", state)

        self.assertEqual(check.role_capacity[0].available_hours, 20)
        self.assertFalse(check.can_complete)

    def test_planner_enforces_both_team_buckets_and_global_total(self) -> None:
        data = context(
            [engineer("E-1", "Team-A", 0.5), engineer("E-1", "Team-B", 0.5)],
            tasks=[task("A", "Team-A", 100), task("B", "Team-B", 90)],
            efforts=[
                {"task_id": "A", "role": "Backend", "required_hours": 60},
                {"task_id": "B", "role": "Backend", "required_hours": 20},
            ],
        )
        plan = build_quarter_plan(data)
        first_sprint = plan.sprints[0]

        self.assertEqual(result(plan, "A").completion_sprint, 1)
        self.assertEqual(result(plan, "B").completion_sprint, 1)
        self.assertEqual(first_sprint.engineer_hours_used["E-1"], 80)
        self.assertEqual(
            first_sprint.engineer_team_hours_used["E-1"],
            {"Team-A": 40, "Team-B": 40},
        )
        self.assertTrue(
            all(hours <= 40 for hours in first_sprint.engineer_team_hours_used["E-1"].values())
        )

    def test_duplicate_same_team_profile_does_not_add_capacity(self) -> None:
        duplicate = engineer("E-1", "Team-A", 0.5)
        data = context([duplicate, dict(duplicate)], hours=60)
        check = FeasibilityService(data).check("T", SprintState(1))

        self.assertEqual(check.role_capacity[0].available_hours, 40)
        self.assertEqual(len(check.engineers), 1)
        plan = build_quarter_plan(data)
        self.assertTrue(
            all(
                sprint.engineer_team_hours_used.get("E-1", {}).get("Team-A", 0)
                <= 40
                for sprint in plan.sprints
            )
        )

    def test_external_wrong_role_cannot_replace_required_role(self) -> None:
        data = context([engineer("EXT", "Team-B", role="Backend")], role="Java")
        check = FeasibilityService(data).check("T", SprintState(1))
        self.assertFalse(check.can_complete)
        self.assertEqual(check.role_capacity[0].available_hours, 0)
        allocation = allocate_available_hours({"Java": 40}, check)
        self.assertEqual(allocation.total_hours, 0)
        blocked = result(build_quarter_plan(data))
        self.assertEqual(blocked.status, PlanStatus.INELIGIBLE_DATA_CONSTRAINT)
        self.assertIn(ReasonCode.NO_AVAILABLE_ROLE_SPECIALIST, [r.code for r in blocked.reasons])

    def test_story_points_use_only_task_team_and_respect_its_limit(self) -> None:
        data = context(
            [
                engineer("LOCAL", "Team-A", 0.75),
                engineer("EXT", "Team-B"),
            ],
            hours=80,
        )
        data = replace(data, team_history=(
            {"team_id": "Team-A", "velocity_achieved": 2.5},
            {"team_id": "Team-B", "velocity_achieved": 100},
        ))
        check = FeasibilityService(data).check("T", SprintState(1))
        self.assertTrue(check.can_complete)
        self.assertEqual(check.team_capacity.available_sp, 2)
        plan = build_quarter_plan(data, allow_team_reassignment=False)
        self.assertEqual(result(plan).completion_sprint, 1)
        self.assertEqual([s.team_sp_used.get("Team-A", 0) for s in plan.sprints], [2, 0, 0, 0, 0, 0])
        self.assertTrue(all(s.team_sp_used.get("Team-B", 0) == 0 for s in plan.sprints))
        self.assertTrue(any(
            item.cross_team
            for allocation in result(plan).allocations
            for item in allocation.engineer_allocations
        ))

    def test_external_engineer_can_complete_work_across_sprints(self) -> None:
        plan = build_quarter_plan(context([engineer("EXT", "Team-B")], hours=130))
        planned = result(plan)
        self.assertEqual(planned.status, PlanStatus.PLANNED)
        self.assertEqual(planned.assigned_team_id, "Team-B")
        self.assertEqual(planned.completion_sprint, 2)
        self.assertAlmostEqual(planned.allocations[0].allocated_hours, 80)
        self.assertAlmostEqual(planned.allocations[1].allocated_hours, 50)
        self.assertTrue(all(
            not e.cross_team for a in planned.allocations for e in a.engineer_allocations
        ))
        self.assertTrue(all(s.engineer_hours_used.get("EXT", 0) <= 80 for s in plan.sprints))

    def test_unsuccessful_multi_sprint_attempt_rolls_back_local_and_external_hours(self) -> None:
        data = context(
            [engineer("LOCAL", "Team-A"), engineer("EXT", "Team-B")],
            tasks=[task("HUGE", rung=100), task("NEXT", rung=0)],
            efforts=[
                {"task_id": "HUGE", "role": "Backend", "required_hours": 1000},
                {"task_id": "NEXT", "role": "Backend", "required_hours": 160},
            ],
        )
        plan = build_quarter_plan(data)
        huge, following = result(plan, "HUGE"), result(plan, "NEXT")
        self.assertEqual(huge.status, PlanStatus.MOVED)
        self.assertEqual(huge.allocations, ())
        self.assertTrue(any(
            a.outcome == PlacementOutcome.ROLLED_BACK and a.allocated_hours == 160
            for a in huge.placement_attempts
        ))
        self.assertEqual(following.completion_sprint, 2)
        self.assertEqual(plan.sprints[0].engineer_hours_used, {"LOCAL": 80})
        self.assertEqual(plan.sprints[1].engineer_hours_used, {"LOCAL": 80})
        self.assertNotIn("EXT", plan.sprints[0].engineer_hours_used)
        self.assertTrue(all(not s.engineer_hours_used and not s.team_sp_used for s in plan.sprints[2:]))
        self.assertTrue(all(a.task_id == "NEXT" for s in plan.sprints for a in s.task_allocations))

    def test_selection_is_stable_by_locality_free_hours_then_id(self) -> None:
        profiles = [
            engineer("Z-LOCAL", "Team-A", 0.5),
            engineer("B-EXT", "Team-B"),
            engineer("A-EXT", "Team-C"),
            engineer("C-EXT", "Team-B", 0.5),
        ]
        plans = [build_quarter_plan(context(rows, hours=180)) for rows in (profiles, profiles[::-1])]
        self.assertEqual(plans[0].to_dict(), plans[1].to_dict())
        allocations = result(plans[0]).allocations[0].engineer_allocations
        self.assertEqual(result(plans[0]).assigned_team_id, "Team-B")
        self.assertEqual(
            [(a.engineer_id, a.hours) for a in allocations],
            [("B-EXT", 80), ("C-EXT", 40), ("A-EXT", 60)],
        )

    def test_cross_team_does_not_bypass_supplied_skill_requirements(self) -> None:
        data = context([engineer("EXT", "Team-B")])
        data = replace(
            data,
            engineer_skills=({"engineer_id": "EXT", "technology": "Python"},),
            task_skill_requirements=({"task_id": "T", "role": "Backend", "technology": "Go"},),
        )
        check = FeasibilityService(data).check("T", SprintState(1))
        self.assertEqual(check.role_capacity[0].available_hours, 0)
        self.assertEqual(allocate_available_hours({"Backend": 40}, check).total_hours, 0)
        self.assertIn(ReasonCode.INSUFFICIENT_SKILL_CAPACITY, [r.code for r in check.reasons])
        matching = replace(data, engineer_skills=({"engineer_id": "EXT", "technology": "Go"},))
        self.assertTrue(FeasibilityService(matching).check("T", SprintState(1)).can_complete)

    def test_eligibility_does_not_check_free_sprint_capacity(self) -> None:
        data = context([engineer("EXT", "Team-B")])
        eligibility = EligibilityService(EligibilityContext.from_records(
            task_role_effort=data.task_role_effort, engineers=data.engineers,
        ))
        self.assertTrue(eligibility.check(data.tasks[0]).eligible)
        busy = FeasibilityService(data).check("T", SprintState(1, engineer_hours_used={"EXT": 80}))
        self.assertFalse(busy.can_start)
        self.assertEqual(busy.role_capacity[0].available_hours, 0)

    def test_allocation_metadata_keeps_all_home_teams_and_task_team(self) -> None:
        data = context([
            engineer("LOCAL", "Team-A", 0.5), engineer("LOCAL", "Team-C", 0.5),
            engineer("EXT", "Team-B"),
        ], hours=60)
        allocations = result(build_quarter_plan(data)).allocations[0].engineer_allocations
        local, external = [a.to_dict() for a in allocations]
        self.assertEqual(local["teams"], ["Team-A", "Team-C"])
        self.assertFalse(local["cross_team"])
        self.assertEqual(external["teams"], ["Team-B"])
        self.assertEqual(external["task_team"], "Team-A")
        self.assertEqual(external["role"], "Backend")
        self.assertEqual(external["allocated_hours"], 20)
        self.assertEqual(external["hours"], external["allocated_hours"])
        self.assertTrue(external["cross_team"])


if __name__ == "__main__":
    unittest.main()
