from __future__ import annotations

import unittest

from src.planning import FeasibilityContext, PlanStatus, build_quarter_plan
from src.planning.capacity import calculate_engineer_capacity
from src.planning.plan_models import PlacementOutcome, RecommendationType
from src.planning.planner import sort_tasks_for_planning
from src.planning.reasons import ReasonCode
from src.planning.scoring import PlanningContext, calculate_task_score
from src.planning.story_points import distribute_capacity_sp_load


def task(
    task_id: str,
    *,
    team: str | None = "Team-A",
    story_points: float | None = 2,
    status: str = "ToDo",
    rung: float = 80,
    placeholder: bool = False,
    planned_start: str | None = None,
    planned_end: str | None = None,
    initiative_id: str | None = "PRODF-1",
) -> dict:
    return {
        "task_id": task_id,
        "initiative_id": initiative_id if not placeholder else None,
        "team_id": team,
        "story_points": story_points,
        "status": status,
        "rung": rung,
        "planned_start": planned_start,
        "planned_end": planned_end,
        "is_placeholder": placeholder,
    }


def context(
    tasks: list[dict],
    *,
    effort: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    engineers: list[dict] | None = None,
    history: list[dict] | None = None,
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=tasks,
        task_role_effort=effort or [],
        dependencies=dependencies or [],
        engineers=engineers
        or [
            {
                "engineer_id": "E-1",
                "team_id": "Team-A",
                "role": "Backend",
                "capacity_rate": 1,
            }
        ],
        engineer_skills=[],
        team_history=history
        or [
            {"team_id": "Team-A", "velocity_achieved": 10},
            {"team_id": "Team-B", "velocity_achieved": 10},
        ],
    )


def result_by_id(plan, task_id: str):
    return next(item for item in plan.task_results if item.task_id == task_id)


def effort(task_id: str, hours: float, role: str = "Backend") -> dict:
    return {"task_id": task_id, "role": role, "required_hours": hours}


class QuarterPlannerTests(unittest.TestCase):
    def test_creates_exactly_six_sprints(self) -> None:
        plan = build_quarter_plan(context([task("T-1")]))
        self.assertEqual([s.sprint_number for s in plan.sprints], [1, 2, 3, 4, 5, 6])

    def test_public_api_builds_state_without_manual_sprint_state(self) -> None:
        plan = build_quarter_plan(context([task("T-1")]))
        self.assertEqual(result_by_id(plan, "T-1").status, PlanStatus.PLANNED)

    def test_started_task_updates_team_story_points(self) -> None:
        plan = build_quarter_plan(context([task("T-1", story_points=3)]))
        self.assertEqual(plan.sprints[0].team_sp_used["Team-A"], 3)

    def test_team_story_points_never_exceed_capacity(self) -> None:
        plan = build_quarter_plan(
            context([task("T-1", story_points=6), task("T-2", story_points=6)])
        )
        for sprint in plan.sprints:
            for team_id, used in sprint.team_sp_used.items():
                self.assertLessEqual(used, sprint.team_capacity_sp[team_id])
        second = result_by_id(plan, "T-2")
        self.assertEqual((second.start_sprint, second.completion_sprint), (1, 1))
        self.assertTrue(second.team_reassigned)
        self.assertEqual(second.original_team_id, "Team-A")
        self.assertEqual(second.assigned_team_id, "Team-B")
        self.assertEqual([a.capacity_sp_load for a in second.allocations], [6])

    def test_allocated_work_updates_engineer_hours(self) -> None:
        plan = build_quarter_plan(
            context([task("T-1")], effort=[effort("T-1", 24)])
        )
        self.assertEqual(plan.sprints[0].engineer_hours_used["E-1"], 24)

    def test_engineer_hours_never_exceed_nominal_capacity(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("T-1"), task("T-2")],
                effort=[effort("T-1", 60), effort("T-2", 60)],
            )
        )
        nominal = calculate_engineer_capacity(
            {"engineer_id": "E-1", "capacity_rate": 1}
        ).nominal_hours
        self.assertTrue(
            all(s.engineer_hours_used.get("E-1", 0) <= nominal for s in plan.sprints)
        )

    def test_part_time_engineer_preserves_capacity_for_each_team(self) -> None:
        engineers = [
            {"engineer_id": "E-S", "team_id": "Team-A", "role": "Shared", "capacity_rate": 0.5},
            {"engineer_id": "E-S", "team_id": "Team-B", "role": "Shared", "capacity_rate": 0.5},
        ]
        plan = build_quarter_plan(
            context(
                [task("A", team="Team-A"), task("B", team="Team-B")],
                effort=[effort("A", 30, "Shared"), effort("B", 30, "Shared")],
                engineers=engineers,
            )
        )
        self.assertEqual(plan.sprints[0].engineer_hours_used["E-S"], 60)
        self.assertTrue(all(s.engineer_hours_used.get("E-S", 0) <= 80 for s in plan.sprints))
        self.assertTrue(all(
            hours <= 40
            for sprint in plan.sprints
            for hours in sprint.engineer_team_hours_used.get("E-S", {}).values()
        ))

    def test_full_time_engineer_shares_one_capacity_between_two_teams(self) -> None:
        engineers = [
            {"engineer_id": "E-S", "team_id": "Team-A", "role": "Shared", "capacity_rate": 1},
            {"engineer_id": "E-S", "team_id": "Team-B", "role": "Shared", "capacity_rate": 1},
        ]
        plan = build_quarter_plan(
            context(
                [task("A", team="Team-A", rung=100), task("B", team="Team-B", rung=90)],
                effort=[effort("A", 40, "Shared"), effort("B", 40, "Shared")],
                engineers=engineers,
            )
        )
        self.assertEqual(plan.sprints[0].engineer_hours_used["E-S"], 80)
        self.assertTrue(all(s.engineer_hours_used.get("E-S", 0) <= 80 for s in plan.sprints))

    def test_planned_blocker_finishing_sprint_one_requires_sprint_two_gap(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("BLOCKER"), task("BLOCKED")],
                dependencies=[{"blocking_task_id": "BLOCKER", "blocked_task_id": "BLOCKED"}],
            )
        )
        self.assertEqual(result_by_id(plan, "BLOCKER").completion_sprint, 1)
        self.assertEqual(result_by_id(plan, "BLOCKED").start_sprint, 3)

    def test_multi_sprint_blocker_uses_completion_then_full_gap_sprint(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("BLOCKER"), task("BLOCKED")],
                effort=[effort("BLOCKER", 100)],
                dependencies=[{"blocking_task_id": "BLOCKER", "blocked_task_id": "BLOCKED"}],
            )
        )
        self.assertEqual(result_by_id(plan, "BLOCKER").completion_sprint, 2)
        self.assertEqual(result_by_id(plan, "BLOCKED").start_sprint, 4)

    def test_dependency_chain_applies_gap_at_every_edge(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("A"), task("B"), task("C")],
                effort=[effort("A", 10), effort("B", 10), effort("C", 10)],
                dependencies=[
                    {"blocking_task_id": "A", "blocked_task_id": "B"},
                    {"blocking_task_id": "B", "blocked_task_id": "C"},
                ],
            )
        )
        self.assertEqual(result_by_id(plan, "A").completion_sprint, 1)
        self.assertEqual(result_by_id(plan, "B").start_sprint, 3)
        self.assertEqual(result_by_id(plan, "B").completion_sprint, 3)
        self.assertEqual(result_by_id(plan, "C").start_sprint, 5)

    def test_done_blocker_allows_dependent_in_first_sprint(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("DONE", status="Done"), task("NEXT")],
                dependencies=[{"blocking_task_id": "DONE", "blocked_task_id": "NEXT"}],
            )
        )
        self.assertEqual(result_by_id(plan, "DONE").completion_sprint, 0)
        self.assertEqual(result_by_id(plan, "NEXT").start_sprint, 1)

    def test_placeholder_is_excluded_from_plan(self) -> None:
        plan = build_quarter_plan(
            context([task("MISSING", team=None, story_points=None, placeholder=True)])
        )
        placeholder = result_by_id(plan, "MISSING")
        self.assertEqual(placeholder.status, PlanStatus.UNKNOWN)
        self.assertEqual(placeholder.allocations, ())

    def test_placeholder_does_not_block_other_valid_tasks(self) -> None:
        plan = build_quarter_plan(
            context(
                [
                    task("MISSING", team=None, story_points=None, placeholder=True),
                    task("VALID"),
                ]
            )
        )
        self.assertEqual(result_by_id(plan, "MISSING").status, PlanStatus.UNKNOWN)
        self.assertEqual(result_by_id(plan, "VALID").status, PlanStatus.PLANNED)

    def test_dependency_on_placeholder_moves_dependent_task(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("MISSING", team=None, story_points=None, placeholder=True), task("NEXT")],
                dependencies=[{"blocking_task_id": "MISSING", "blocked_task_id": "NEXT"}],
            )
        )
        moved = result_by_id(plan, "NEXT")
        self.assertEqual(moved.status, PlanStatus.MOVED)
        self.assertIn(ReasonCode.DEPENDENCY_NOT_COMPLETED, {r.code for r in moved.reasons})
        self.assertIn(ReasonCode.UNKNOWN_DEPENDENCY_DATE, {r.code for r in moved.reasons})
        self.assertNotIn(ReasonCode.OUTSIDE_QUARTER, {r.code for r in moved.reasons})
        self.assertFalse(moved.outside_quarter)

    def test_missing_dependency_reference_has_separate_reason(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("NEXT")],
                dependencies=[{"blocking_task_id": "ABSENT", "blocked_task_id": "NEXT"}],
            )
        )
        moved = result_by_id(plan, "NEXT")
        codes = {reason.code for reason in moved.final_reasons}
        self.assertIn(ReasonCode.MISSING_DEPENDENCY_REFERENCE, codes)
        self.assertIn(ReasonCode.DEPENDENCY_NOT_COMPLETED, codes)
        self.assertNotIn(ReasonCode.OUTSIDE_QUARTER, codes)
        self.assertFalse(moved.outside_quarter)

    def test_successful_multi_sprint_task_commits_every_allocation(self) -> None:
        plan = build_quarter_plan(
            context([task("BIG")], effort=[effort("BIG", 130)])
        )
        big = result_by_id(plan, "BIG")
        self.assertEqual(big.completion_sprint, 2)
        self.assertAlmostEqual(big.allocations[0].allocated_hours, 80)
        self.assertAlmostEqual(big.allocations[1].allocated_hours, 50)
        self.assertTrue(all(a.outcome == PlacementOutcome.COMMITTED for a in big.placement_attempts))
        self.assertEqual(sum(len(s.task_allocations) for s in plan.sprints), 2)

    def test_impossible_todo_leaves_no_story_points_after_rollback(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        self.assertEqual(result_by_id(plan, "HUGE").status, PlanStatus.MOVED)
        self.assertTrue(all(not sprint.team_sp_used for sprint in plan.sprints))

    def test_impossible_todo_leaves_no_engineer_hours_after_rollback(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        self.assertTrue(all(not sprint.engineer_hours_used for sprint in plan.sprints))

    def test_failed_multi_sprint_simulation_is_fully_rolled_back(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        huge = result_by_id(plan, "HUGE")
        self.assertEqual(huge.allocations, ())
        self.assertTrue(
            any(a.outcome == PlacementOutcome.ROLLED_BACK for a in huge.placement_attempts)
        )
        self.assertIn(ReasonCode.ALLOCATION_ROLLED_BACK, {r.code for r in huge.final_reasons})

    def test_next_task_uses_resources_released_by_rollback(self) -> None:
        tasks = [
            task("HUGE", planned_start="2026-06-01"),
            task("NEXT", planned_start="2026-06-02"),
        ]
        plan = build_quarter_plan(
            context(tasks, effort=[effort("HUGE", 500), effort("NEXT", 80)])
        )
        self.assertEqual(result_by_id(plan, "HUGE").status, PlanStatus.MOVED)
        self.assertEqual(result_by_id(plan, "NEXT").start_sprint, 1)
        self.assertEqual(plan.sprints[0].engineer_hours_used["E-1"], 80)

    def test_moved_task_keeps_full_uncommitted_requirement(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        self.assertEqual(result_by_id(plan, "HUGE").remaining_role_hours, {"Backend": 500})

    def test_two_sprint_task_completes_all_story_points_in_final_sprint(self) -> None:
        engineers = [
            {"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 0.75}
        ]
        plan = build_quarter_plan(
            context(
                [task("BIG", story_points=8)],
                effort=[effort("BIG", 100)],
                engineers=engineers,
                history=[{"team_id": "Team-A", "velocity_achieved": 20}],
            )
        )
        allocations = result_by_id(plan, "BIG").allocations
        self.assertEqual([a.allocated_hours for a in allocations], [60, 40])
        self.assertEqual([a.capacity_sp_load for a in allocations], [4.8, 3.2])
        self.assertEqual([a.completed_sp for a in allocations], [0, 0])
        self.assertEqual([a.planned_completion_sp for a in allocations], [0, 8])
        self.assertEqual([s.completed_sp for s in plan.sprints[:2]], [0, 0])

    def test_sp_capacity_limits_hours_for_multi_sprint_task(self) -> None:
        engineers = [
            {"engineer_id": "BE-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
            {"engineer_id": "BE-2", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
            {"engineer_id": "QA-1", "team_id": "Team-A", "role": "QA", "capacity_rate": 1},
        ]
        plan = build_quarter_plan(
            context(
                [
                    task("FIRST", story_points=5, rung=100),
                    task("AI-302", story_points=9, rung=90),
                ],
                effort=[effort("FIRST", 10, "QA"), effort("AI-302", 216)],
                engineers=engineers,
            )
        )

        allocations = result_by_id(plan, "AI-302").allocations
        self.assertEqual([a.allocated_hours for a in allocations], [72, 144])
        self.assertEqual([a.capacity_sp_load for a in allocations], [3, 6])

    def test_multi_sprint_hours_and_sp_are_proportional(self) -> None:
        plan = build_quarter_plan(
            context([task("BIG", story_points=5)], effort=[effort("BIG", 170)])
        )
        allocations = result_by_id(plan, "BIG").allocations
        hours_per_sp = 170 / 5

        self.assertGreater(len(allocations), 1)
        self.assertTrue(
            all(
                abs(a.allocated_hours - a.capacity_sp_load * hours_per_sp) <= 1e-9
                for a in allocations
            )
        )
        self.assertEqual(sum(a.capacity_sp_load for a in allocations), 5)
        self.assertTrue(all(a.capacity_sp_load > 0 for a in allocations))

    def test_forecast_keeps_completed_sp_zero_and_preserves_capacity_sp(self) -> None:
        plan = build_quarter_plan(
            context([task("BIG", story_points=5)], effort=[effort("BIG", 130)])
        )
        self.assertEqual(
            sum(a.completed_sp for a in result_by_id(plan, "BIG").allocations),
            0,
        )
        self.assertEqual(
            sum(a.capacity_sp_load for a in result_by_id(plan, "BIG").allocations),
            5,
        )
        self.assertEqual(result_by_id(plan, "BIG").completed_sp, 0)

    def test_three_sprint_task_has_zero_sp_until_final_allocation(self) -> None:
        engineers = [
            {"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1}
        ]
        plan = build_quarter_plan(
            context([task("THIRD", story_points=5)], effort=[effort("THIRD", 170)], engineers=engineers)
        )
        allocations = result_by_id(plan, "THIRD").allocations
        self.assertEqual(len(allocations), 3)
        self.assertAlmostEqual(allocations[0].allocated_hours, 80)
        self.assertAlmostEqual(allocations[1].allocated_hours, 80)
        self.assertAlmostEqual(allocations[2].allocated_hours, 10)
        for allocation in allocations:
            self.assertAlmostEqual(
                allocation.capacity_sp_load,
                5 * allocation.allocated_hours / 170,
            )
        self.assertEqual([a.completed_sp for a in allocations], [0, 0, 0])
        self.assertEqual([a.planned_completion_sp for a in allocations], [0, 0, 5])

    def test_fractional_capacity_sp_respects_team_capacity(self) -> None:
        plan = build_quarter_plan(
            context([task("BIG", story_points=8)], effort=[effort("BIG", 200)])
        )
        allocations = result_by_id(plan, "BIG").allocations
        self.assertAlmostEqual(allocations[0].capacity_sp_load, 3.2)
        self.assertAlmostEqual(allocations[1].capacity_sp_load, 3.2)
        self.assertAlmostEqual(allocations[2].capacity_sp_load, 1.6)
        self.assertEqual([a.completed_sp for a in allocations], [0, 0, 0])
        self.assertAlmostEqual(sum(a.capacity_sp_load for a in allocations), 8)
        for sprint in plan.sprints:
            self.assertLessEqual(
                sprint.team_sp_used.get("Team-A", 0), sprint.team_capacity_sp["Team-A"]
            )

    def test_single_sprint_task_sets_planned_completion_but_not_actual_sp(self) -> None:
        plan = build_quarter_plan(
            context([task("SMALL", story_points=5)], effort=[effort("SMALL", 40)])
        )
        allocation = result_by_id(plan, "SMALL").allocations[0]
        self.assertEqual(allocation.task_story_points, 5)
        self.assertEqual(allocation.capacity_sp_load, 5)
        self.assertEqual(allocation.planned_completion_sp, 5)
        self.assertEqual(allocation.completed_sp, 0)
        self.assertTrue(allocation.is_completed)
        self.assertEqual(plan.sprints[0].completed_sp, 0)

    def test_source_done_task_exposes_full_actual_completed_sp(self) -> None:
        plan = build_quarter_plan(
            context([task("DONE", story_points=5, status="Done")])
        )
        completed = result_by_id(plan, "DONE")
        self.assertEqual(completed.status, PlanStatus.COMPLETED)
        self.assertEqual(completed.completed_sp, 5)

    def test_full_story_points_wait_for_owner_team_capacity(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("A", story_points=8, rung=100), task("B", story_points=2, rung=50)],
                effort=[effort("A", 10), effort("B", 10)],
            )
        )
        delayed = result_by_id(plan, "B")
        self.assertEqual((delayed.start_sprint, delayed.completion_sprint), (2, 2))
        self.assertEqual(
            [
                (
                    a.sprint_number,
                    a.allocated_hours,
                    a.capacity_sp_load,
                    a.completed_sp,
                )
                for a in delayed.allocations
            ],
            [(2, 10, 2, 0)],
        )
        self.assertEqual(plan.sprints[0].team_sp_used["Team-A"], 8)
        self.assertEqual(plan.sprints[1].team_sp_used["Team-A"], 2)
        self.assertTrue(all(sprint.team_sp_used.get("Team-B", 0) == 0 for sprint in plan.sprints))
        self.assertFalse(delayed.team_reassigned)

    def test_initiative_count_cannot_override_absolute_task_rung(self) -> None:
        tasks = [
            task("HIGH", story_points=30, rung=10, initiative_id="INIT-HIGH"),
            task("LOW-A", story_points=24, rung=5, initiative_id="INIT-LOW-A"),
            task("LOW-B", story_points=24, rung=5, initiative_id="INIT-LOW-B"),
        ]
        plan = build_quarter_plan(
            context(
                tasks,
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        self.assertEqual(result_by_id(plan, "HIGH").status, PlanStatus.PLANNED)
        self.assertEqual(result_by_id(plan, "HIGH").priority_order, 1)
        self.assertNotEqual(
            {
                result_by_id(plan, "LOW-A").status,
                result_by_id(plan, "LOW-B").status,
            },
            {PlanStatus.PLANNED},
        )
        self.assertIn("INIT-HIGH", plan.completed_initiative_ids)

    def test_rung_breaks_tie_between_equally_completable_initiatives(self) -> None:
        plan = build_quarter_plan(
            context(
                [
                    task("LOW", story_points=30, rung=10, initiative_id="INIT-LOW"),
                    task("HIGH", story_points=30, rung=100, initiative_id="INIT-HIGH"),
                ],
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        self.assertEqual(plan.completed_initiative_ids, ("INIT-HIGH",))
        self.assertEqual(result_by_id(plan, "HIGH").status, PlanStatus.PLANNED)

    def test_task_is_reassigned_only_to_existing_team_when_source_is_infeasible(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("MOVE", team="Team-A", story_points=4)],
                effort=[effort("MOVE", 40)],
                engineers=[
                    {
                        "engineer_id": "E-B",
                        "team_id": "Team-B",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[
                    {"team_id": "Team-A", "velocity_achieved": 0},
                    {"team_id": "Team-B", "velocity_achieved": 10},
                ],
            )
        )
        moved = result_by_id(plan, "MOVE")

        self.assertEqual(moved.original_team_id, "Team-A")
        self.assertEqual(moved.assigned_team_id, "Team-B")
        self.assertTrue(moved.team_reassigned)
        self.assertEqual(moved.team_assignment_reason, "ORIGINAL_TEAM_INFEASIBLE")
        self.assertEqual(plan.sprints[0].team_sp_used, {"Team-B": 4})
        self.assertIn(ReasonCode.TEAM_REASSIGNED, {reason.code for reason in moved.reasons})
        self.assertEqual(
            moved.recommendations[0].recommendation_type,
            RecommendationType.REASSIGN_TO_EXISTING_TEAM,
        )
        self.assertTrue(moved.recommendations[0].available)
        self.assertEqual(moved.recommendations[0].target_team_id, "Team-B")

    def test_task_stays_with_source_team_when_both_finish_equally(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("STAY", team="Team-A", story_points=4)],
                effort=[effort("STAY", 40)],
            )
        )
        stayed = result_by_id(plan, "STAY")

        self.assertEqual(stayed.original_team_id, "Team-A")
        self.assertEqual(stayed.assigned_team_id, "Team-A")
        self.assertFalse(stayed.team_reassigned)
        self.assertIn(stayed.assigned_team_id, {"Team-A", "Team-B"})

    def test_task_cannot_be_assigned_to_nonexistent_source_team(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("UNKNOWN-TEAM", team="Team-New", story_points=4)],
                effort=[effort("UNKNOWN-TEAM", 40)],
                engineers=[
                    {
                        "engineer_id": "E-B",
                        "team_id": "Team-B",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[
                    {"team_id": "Team-A", "velocity_achieved": 10},
                    {"team_id": "Team-B", "velocity_achieved": 10},
                ],
            )
        )
        planned = result_by_id(plan, "UNKNOWN-TEAM")

        self.assertEqual(planned.original_team_id, "Team-New")
        self.assertIn(planned.assigned_team_id, {"Team-A", "Team-B"})
        self.assertNotEqual(planned.assigned_team_id, "Team-New")

    def test_task_larger_than_one_sprint_capacity_is_stretched(self) -> None:
        plan = build_quarter_plan(
            context([task("TOO-LARGE", story_points=9)], effort=[effort("TOO-LARGE", 40)])
        )
        planned = result_by_id(plan, "TOO-LARGE")
        self.assertEqual(planned.status, PlanStatus.PLANNED)
        self.assertEqual((planned.start_sprint, planned.completion_sprint), (1, 2))
        self.assertEqual([a.capacity_sp_load for a in planned.allocations], [8, 1])
        self.assertEqual([a.completed_sp for a in planned.allocations], [0, 0])

    def test_capacity_sp_load_is_fully_rolled_back_when_quarter_is_too_short(self) -> None:
        plan = build_quarter_plan(
            context([task("TOO-LARGE", story_points=49)], effort=[effort("TOO-LARGE", 40)])
        )
        moved = result_by_id(plan, "TOO-LARGE")
        self.assertEqual(moved.status, PlanStatus.MOVED)
        self.assertEqual(moved.completed_sp, 0)
        self.assertEqual(moved.capacity_sp_load, 0)
        self.assertEqual(moved.allocations, ())
        self.assertTrue(all(not sprint.team_sp_used for sprint in plan.sprints))
        self.assertTrue(all(not sprint.engineer_hours_used for sprint in plan.sprints))
        self.assertTrue(all(attempt.completed_sp == 0 for attempt in moved.placement_attempts))

    def test_capacity_distribution_is_proportional_to_hours(self) -> None:
        distribution = distribute_capacity_sp_load(
            task_story_points=8,
            active_sprint_hours={1: 60, 2: 40},
            available_sp_by_sprint={1: 8, 2: 8},
            last_sprint=6,
        )
        self.assertEqual(distribution.capacity_sp_load, {1: 4.8, 2: 3.2})
        self.assertEqual(distribution.remaining_sp, 0)
        self.assertEqual(sum(distribution.capacity_sp_load.values()), 8)

    def test_capacity_distribution_neither_loses_nor_creates_sp_under_caps(self) -> None:
        distribution = distribute_capacity_sp_load(
            task_story_points=9,
            active_sprint_hours={1: 192, 2: 24},
            available_sp_by_sprint={1: 8, 2: 8},
            last_sprint=6,
        )
        self.assertEqual(distribution.capacity_sp_load, {1: 8, 2: 1})
        self.assertEqual(sum(distribution.capacity_sp_load.values()), 9)
        self.assertTrue(all(load <= 8 for load in distribution.capacity_sp_load.values()))

    def test_three_sprint_example_keeps_fractional_sp_proportional(self) -> None:
        distribution = distribute_capacity_sp_load(
            task_story_points=10,
            active_sprint_hours={1: 80, 2: 120, 3: 100},
            available_sp_by_sprint={1: 10, 2: 10, 3: 10},
            last_sprint=6,
        )
        self.assertAlmostEqual(distribution.capacity_sp_load[1], 8 / 3)
        self.assertAlmostEqual(distribution.capacity_sp_load[2], 4)
        self.assertAlmostEqual(distribution.capacity_sp_load[3], 10 / 3)
        self.assertAlmostEqual(sum(distribution.capacity_sp_load.values()), 10)

    def test_unplaceable_task_has_next_quarter_recommendation(self) -> None:
        moved = result_by_id(
            build_quarter_plan(context([task("HUGE")], effort=[effort("HUGE", 500)])),
            "HUGE",
        )
        recommendations = {
            item.recommendation_type: item for item in moved.recommendations
        }
        self.assertFalse(
            recommendations[RecommendationType.REASSIGN_TO_EXISTING_TEAM].available
        )
        self.assertTrue(
            recommendations[RecommendationType.MOVE_TO_NEXT_QUARTER].available
        )

    def test_done_task_with_open_blocker_remains_fact_without_resources(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("BLOCKER", status="ToDo"), task("FACT", status="Done")],
                effort=[effort("FACT", 80)],
                dependencies=[
                    {"blocking_task_id": "BLOCKER", "blocked_task_id": "FACT"}
                ],
            )
        )
        fact = result_by_id(plan, "FACT")
        self.assertEqual(fact.status, PlanStatus.COMPLETED)
        self.assertEqual(fact.completion_sprint, 0)
        self.assertEqual(fact.allocations, ())

    def test_sp_without_hours_uses_explicit_single_sprint_fallback(self) -> None:
        plan = build_quarter_plan(context([task("NO-HOURS", story_points=3)]))
        result = result_by_id(plan, "NO-HOURS")
        self.assertEqual(result.allocations[0].completed_sp, 0)
        self.assertIn(ReasonCode.STORY_POINTS_WITHOUT_HOURS, {r.code for r in result.final_reasons})

    def test_score_can_put_high_value_todo_before_in_progress(self) -> None:
        ordered = sort_tasks_for_planning(
            [
                task("LATE", rung=99, planned_start="2026-07-01"),
                task("EARLY", rung=1, planned_start="2026-06-01"),
                task("ACTIVE", status="InProgress", rung=1, planned_start="2026-08-01"),
            ],
            [],
        )
        self.assertEqual([row["task_id"] for row in ordered], ["LATE", "EARLY", "ACTIVE"])

    def test_absolute_rung_wins_when_quarter_capacity_fits_only_one_task(self) -> None:
        plan = build_quarter_plan(
            context(
                [
                    task("LOW", story_points=30, rung=5),
                    task("HIGH", story_points=30, rung=10),
                ],
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        self.assertEqual(result_by_id(plan, "HIGH").status, PlanStatus.PLANNED)
        self.assertEqual(result_by_id(plan, "HIGH").priority_order, 1)
        self.assertEqual(result_by_id(plan, "LOW").status, PlanStatus.MOVED)

    def test_infeasible_high_rung_does_not_block_feasible_lower_rung(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("HIGH", rung=10), task("LOW", rung=8)],
                effort=[
                    effort("HIGH", 40, "Missing role"),
                    effort("LOW", 40, "Backend"),
                ],
            )
        )

        self.assertEqual(
            result_by_id(plan, "HIGH").status,
            PlanStatus.INELIGIBLE_DATA_CONSTRAINT,
        )
        self.assertEqual(result_by_id(plan, "LOW").status, PlanStatus.PLANNED)

    def test_same_rung_earlier_start_date_wins_limited_capacity(self) -> None:
        plan = build_quarter_plan(
            context(
                [
                    task("LATE", story_points=30, rung=8, planned_start="2026-09-15"),
                    task("EARLY", story_points=30, rung=8, planned_start="2026-09-01"),
                ],
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        self.assertEqual(result_by_id(plan, "EARLY").priority_order, 1)
        self.assertEqual(result_by_id(plan, "EARLY").status, PlanStatus.PLANNED)
        self.assertEqual(result_by_id(plan, "LATE").status, PlanStatus.MOVED)

    def test_start_date_is_not_a_hard_resource_constraint(self) -> None:
        plan = build_quarter_plan(
            context(
                [
                    task("BLOCKER", rung=10),
                    task("EARLY", rung=8, planned_start="2026-09-01"),
                    task("LATE", rung=8, planned_start="2026-09-15"),
                ],
                effort=[
                    effort("BLOCKER", 80),
                    effort("EARLY", 80),
                    effort("LATE", 80),
                ],
                dependencies=[
                    {"blocking_task_id": "BLOCKER", "blocked_task_id": "EARLY"}
                ],
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        self.assertEqual(result_by_id(plan, "BLOCKER").start_sprint, 1)
        self.assertEqual(result_by_id(plan, "EARLY").status, PlanStatus.PLANNED)
        self.assertEqual(result_by_id(plan, "EARLY").start_sprint, 3)
        self.assertEqual(result_by_id(plan, "LATE").status, PlanStatus.PLANNED)
        self.assertEqual(result_by_id(plan, "LATE").start_sprint, 2)

    def test_equal_rung_without_dates_is_deterministic_for_reordered_input(self) -> None:
        rows = [task("B", story_points=30, rung=8), task("A", story_points=30, rung=8)]
        plans = [
            build_quarter_plan(
                context(
                    ordered,
                    engineers=[
                        {
                            "engineer_id": "E-1",
                            "team_id": "Team-A",
                            "role": "Backend",
                            "capacity_rate": 1,
                        }
                    ],
                    history=[{"team_id": "Team-A", "velocity_achieved": 10}],
                )
            )
            for ordered in (rows, list(reversed(rows)))
        ]

        for plan in plans:
            self.assertEqual(result_by_id(plan, "A").priority_order, 1)
            self.assertEqual(result_by_id(plan, "A").status, PlanStatus.PLANNED)
            self.assertEqual(result_by_id(plan, "B").status, PlanStatus.MOVED)

    def test_lower_rung_uses_later_sprint_after_higher_rung(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("LOW", rung=7), task("HIGH", rung=10)],
                effort=[effort("LOW", 80), effort("HIGH", 80)],
                engineers=[
                    {
                        "engineer_id": "E-1",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                history=[{"team_id": "Team-A", "velocity_achieved": 10}],
            )
        )

        high = result_by_id(plan, "HIGH")
        low = result_by_id(plan, "LOW")
        self.assertEqual((high.priority_order, high.start_sprint), (1, 1))
        self.assertEqual((low.priority_order, low.start_sprint), (2, 2))
        self.assertEqual((high.status, low.status), (PlanStatus.PLANNED, PlanStatus.PLANNED))

    def test_priority_tie_breaker_is_task_id(self) -> None:
        common = {"planned_start": "2026-06-01", "planned_end": "2026-06-15"}
        ordered = sort_tasks_for_planning([task("B", **common), task("A", **common)], [])
        self.assertEqual([row["task_id"] for row in ordered], ["A", "B"])

    def test_priority_is_deterministic(self) -> None:
        tasks = [task("B"), task("A"), task("C", status="InProgress")]
        self.assertEqual(
            sort_tasks_for_planning(tasks, []), sort_tasks_for_planning(tasks, [])
        )

    def test_score_uses_only_documented_source_fields(self) -> None:
        source = task("A")
        context = PlanningContext.from_records([source], [])
        regular = calculate_task_score(source, context)
        invented = calculate_task_score({**source, "invented_score": 999}, context)
        self.assertEqual(regular.to_dict(), invented.to_dict())

    def test_high_score_infeasible_task_is_rejected_then_next_is_tried(self) -> None:
        tasks = [task("HIGH", rung=100), task("NEXT", rung=0)]
        plan = build_quarter_plan(
            context(tasks, effort=[effort("HIGH", 500), effort("NEXT", 80)])
        )
        high = result_by_id(plan, "HIGH")
        next_task = result_by_id(plan, "NEXT")
        self.assertGreater(high.score.total_score, next_task.score.total_score)
        self.assertEqual(high.priority_order, 1)
        self.assertEqual(high.status, PlanStatus.MOVED)
        self.assertEqual(next_task.priority_order, 2)
        self.assertEqual(next_task.status, PlanStatus.PLANNED)

    def test_score_tie_break_is_deterministic(self) -> None:
        planning_context = context([task("B"), task("A")])
        first = build_quarter_plan(planning_context)
        second = build_quarter_plan(planning_context)
        first_order = [item.task_id for item in sorted(first.task_results, key=lambda item: item.priority_order or 999)]
        second_order = [item.task_id for item in sorted(second.task_results, key=lambda item: item.priority_order or 999)]
        self.assertEqual(first_order, ["A", "B"])
        self.assertEqual(first_order, second_order)

    def test_planned_task_final_reasons_exclude_rejected_insufficiency(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("A", story_points=8), task("B", story_points=2)],
                effort=[effort("A", 80), effort("B", 10)],
            )
        )
        planned = result_by_id(plan, "B")
        self.assertEqual(planned.status, PlanStatus.PLANNED)
        self.assertFalse(
            any(reason.code.value.startswith("INSUFFICIENT_") for reason in planned.final_reasons)
        )

    def test_rejected_attempts_are_stored_separately_with_sprint(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("A", story_points=8), task("B", story_points=2)],
                effort=[effort("A", 80), effort("B", 10)],
            )
        )
        planned = result_by_id(plan, "B")
        self.assertEqual(planned.rejected_attempts[0].sprint_number, 1)
        self.assertIn(
            ReasonCode.INSUFFICIENT_ENGINEER_CAPACITY,
            {reason.code for reason in planned.rejected_attempts[0].reasons},
        )

    def test_resource_shortage_has_role_reason_not_outside_quarter(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        moved = result_by_id(plan, "HUGE")
        self.assertIn(ReasonCode.INSUFFICIENT_ROLE_CAPACITY, {r.code for r in moved.final_reasons})
        self.assertNotIn(ReasonCode.OUTSIDE_QUARTER, {r.code for r in moved.final_reasons})
        self.assertFalse(moved.outside_quarter)

    def test_attempt_history_preserves_sprint_and_reason_codes(self) -> None:
        plan = build_quarter_plan(
            context([task("HUGE")], effort=[effort("HUGE", 500)])
        )
        attempts = result_by_id(plan, "HUGE").to_dict()["placement_attempts"]
        self.assertEqual([item["sprint_number"] for item in attempts], [1, 2, 3, 4, 5, 6])
        self.assertTrue(any(item["reason_codes"] for item in attempts))

    def test_baseline_dates_are_preserved_and_forecast_is_separate(self) -> None:
        source = task(
            "DATED",
            planned_start="2026-06-20 09:00:00",
            planned_end="2026-07-10 18:00:00",
        )
        plan = build_quarter_plan(context([source]), quarter_start_date="2026-06-15")
        result = result_by_id(plan, "DATED")
        self.assertEqual(result.baseline_start_date, source["planned_start"])
        self.assertEqual(result.baseline_end_date, source["planned_end"])
        self.assertEqual(result.forecast_start_date, "2026-06-15")
        self.assertEqual(result.forecast_end_date, "2026-06-28")

    def test_negative_date_shifts_are_calculated(self) -> None:
        source = task("DATED", planned_start="2026-06-20", planned_end="2026-07-10")
        result = result_by_id(
            build_quarter_plan(context([source]), quarter_start_date="2026-06-15"),
            "DATED",
        )
        self.assertEqual((result.start_shift_days, result.end_shift_days), (-5, -12))
        self.assertEqual(result.start_shift_direction, "EARLIER")

    def test_positive_date_shifts_are_calculated(self) -> None:
        tasks = [
            task("A", story_points=8, rung=100, planned_start="2026-06-01"),
            task(
                "B",
                story_points=2,
                rung=0,
                planned_start="2026-06-15",
                planned_end="2026-06-20",
            ),
        ]
        plan = build_quarter_plan(
            context(tasks, effort=[effort("A", 80), effort("B", 10)]),
            quarter_start_date="2026-06-15",
        )
        result = result_by_id(plan, "B")
        self.assertEqual(result.forecast_start_date, "2026-06-29")
        self.assertEqual(result.start_shift_days, 14)
        self.assertEqual(result.end_shift_days, 22)
        self.assertEqual(result.end_shift_direction, "LATER")

    def test_repeated_build_does_not_mutate_baseline(self) -> None:
        source = task("DATED", planned_start="2026-08-01", planned_end="2026-08-10")
        planning_context = context([source])
        first = result_by_id(build_quarter_plan(planning_context, "2026-06-15"), "DATED")
        second = result_by_id(build_quarter_plan(planning_context, "2026-06-15"), "DATED")
        self.assertEqual(first.baseline_start_date, second.baseline_start_date)
        self.assertEqual(source["planned_start"], "2026-08-01")

    def test_planned_dates_do_not_block_earlier_forecast(self) -> None:
        source = task("FUTURE", planned_start="2026-08-01", planned_end="2026-08-15")
        result = result_by_id(
            build_quarter_plan(context([source]), quarter_start_date="2026-06-15"),
            "FUTURE",
        )
        self.assertEqual(result.start_sprint, 1)
        self.assertLess(result.start_shift_days, 0)

    def test_outside_quarter_requires_forecast_beyond_sprint_six(self) -> None:
        plan = build_quarter_plan(
            context(
                [task("BLOCKER", planned_end="2026-07-01"), task("BLOCKED")],
                effort=[effort("BLOCKER", 400)],
                dependencies=[
                    {"blocking_task_id": "BLOCKER", "blocked_task_id": "BLOCKED"}
                ],
            ),
            quarter_start_date="2026-06-15",
        )
        blocker = result_by_id(plan, "BLOCKER")
        result = result_by_id(plan, "BLOCKED")
        self.assertEqual(blocker.completion_sprint, 5)
        self.assertTrue(result.outside_quarter)
        self.assertIsNone(result.forecast_end_date)
        self.assertIn(ReasonCode.OUTSIDE_QUARTER, {r.code for r in result.final_reasons})
        outside_reason = next(
            reason for reason in result.final_reasons if reason.code == ReasonCode.OUTSIDE_QUARTER
        )
        self.assertEqual(outside_reason.details["forecast_completion_sprint_minimum"], 7)
        explanation = next(
            item for item in plan.task_explanations if item.task_id == "BLOCKED"
        )
        delay = next(
            reason
            for reason in explanation.reasons
            if reason.code == ReasonCode.DEPENDENCY_DELAY
        )
        self.assertEqual(delay.details["earliest_allowed_sprint"], 7)
        self.assertIn("после резервного спринта", delay.message)

    def test_missing_quarter_start_does_not_invent_forecast_dates(self) -> None:
        result = result_by_id(build_quarter_plan(context([task("T-1")])), "T-1")
        self.assertIsNone(result.forecast_start_date)
        self.assertIsNone(result.start_shift_days)

    def test_plan_is_deterministic(self) -> None:
        planning_context = context(
            [task("LOW", rung=60), task("HIGH", rung=90)],
            effort=[effort("LOW", 50), effort("HIGH", 50)],
        )
        self.assertEqual(
            build_quarter_plan(planning_context).to_dict(),
            build_quarter_plan(planning_context).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
