from __future__ import annotations

import json
import unittest

from src.planning import FeasibilityContext, build_quarter_plan


def task(task_id: str, *, team: str = "Team-A", story_points: float = 2,
         status: str = "ToDo", initiative_id: str | None = "INIT") -> dict:
    return {"task_id": task_id, "team_id": team, "story_points": story_points,
            "status": status, "initiative_id": initiative_id, "rung": 10}


def context(tasks, *, effort=(), dependencies=(), engineers=None, history=None):
    return FeasibilityContext.from_records(
        tasks=tasks, task_role_effort=effort, dependencies=dependencies,
        engineers=engineers or [{"engineer_id": "E-A", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1}],
        engineer_skills=[],
        team_history=history or [{"team_id": "Team-A", "velocity_achieved": 10},
                                  {"team_id": "Team-B", "velocity_achieved": 10}],
    )


def explanation(plan, task_id):
    return next(item for item in plan.task_explanations if item.task_id == task_id)


class StructuredExplainabilityTests(unittest.TestCase):
    def test_multi_sprint_exposes_reason_and_per_sprint_facts(self):
        plan = build_quarter_plan(context([task("BIG", story_points=5)], effort=[
            {"task_id": "BIG", "role": "Backend", "required_hours": 130},
        ]))
        item = explanation(plan, "BIG")
        self.assertTrue(item.multi_sprint)
        self.assertIn("MULTI_SPRINT", {reason.code.value for reason in item.reasons})
        self.assertEqual(set(item.sp_load_by_sprint), {1, 2})
        self.assertEqual(sum(row["allocated_hours"] for row in item.allocations), 130)

    def test_cross_team_help_is_not_team_reassignment(self):
        plan = build_quarter_plan(context([task("EXT")], effort=[
            {"task_id": "EXT", "role": "Backend", "required_hours": 20},
        ], engineers=[{"engineer_id": "E-B", "team_id": "Team-B", "role": "Backend", "capacity_rate": 1}]))
        item = explanation(plan, "EXT")
        cross = [reason for reason in item.reasons if reason.code.value == "CROSS_TEAM_HELP"]
        self.assertTrue(cross)
        self.assertFalse(item.team_reassigned)
        self.assertNotIn("TEAM_REASSIGNED", {reason.code.value for reason in item.reasons})
        self.assertTrue(cross[0].details["source_team_id"] != item.assigned_team_id)

    def test_reassignment_preserves_original_team(self):
        plan = build_quarter_plan(context([task("MOVE", story_points=4)], effort=[
            {"task_id": "MOVE", "role": "Backend", "required_hours": 20},
        ], engineers=[
            {"engineer_id": "E-A", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
            {"engineer_id": "E-B", "team_id": "Team-B", "role": "Backend", "capacity_rate": 1},
        ], history=[{"team_id": "Team-A", "velocity_achieved": 0},
                    {"team_id": "Team-B", "velocity_achieved": 10}]))
        item = explanation(plan, "MOVE")
        self.assertTrue(item.team_reassigned)
        self.assertEqual(item.original_team_id, "Team-A")
        self.assertEqual(item.assigned_team_id, "Team-B")
        self.assertIn("TEAM_REASSIGNED", {reason.code.value for reason in item.reasons})
        recommendation = next(item for item in item.recommendations if item.code.value == "REASSIGN_TEAM")
        self.assertTrue(recommendation.available)
        self.assertEqual(recommendation.details["to_team"], "Team-B")

    def test_dependency_delay_has_blocker_and_earliest_sprint(self):
        plan = build_quarter_plan(context([task("A"), task("B")], effort=[
            {"task_id": "A", "role": "Backend", "required_hours": 100},
        ], dependencies=[{"blocking_task_id": "A", "blocked_task_id": "B"}]))
        item = explanation(plan, "B")
        delay = next(reason for reason in item.reasons if reason.code.value == "DEPENDENCY_DELAY")
        self.assertEqual(delay.details["blocking_task_id"], "A")
        self.assertEqual(delay.details["earliest_allowed_sprint"], 3)

    def test_resource_deficits_and_overflow_use_specific_explanation_codes(self):
        plan = build_quarter_plan(context([task("TOO_MUCH", story_points=100)], effort=[
            {"task_id": "TOO_MUCH", "role": "Backend", "required_hours": 10},
        ], history=[{"team_id": "Team-A", "velocity_achieved": 1}]))
        codes = {reason.code.value for reason in explanation(plan, "TOO_MUCH").reasons}
        self.assertIn("INSUFFICIENT_SP_CAPACITY", codes)

    def test_role_deficit_stays_a_separate_reason(self):
        plan = build_quarter_plan(context([task("ROLE")], effort=[
            {"task_id": "ROLE", "role": "Backend", "required_hours": 500},
        ]))
        codes = {reason.code.value for reason in explanation(plan, "ROLE").reasons}
        self.assertIn("INSUFFICIENT_ROLE_CAPACITY", codes)

    def test_done_is_accepted_as_fact_and_serializes(self):
        plan = build_quarter_plan(context([task("DONE", status="Done")]))
        item = explanation(plan, "DONE")
        self.assertEqual(item.status.value, "DONE")
        self.assertIn("DONE_STATUS_ACCEPTED_AS_FACT", {reason.code.value for reason in item.reasons})
        payload = plan.to_dict()
        self.assertIn("explanations", payload)
        json.dumps(payload)

    def test_overflow_has_move_recommendation(self):
        tasks = [task(f"T{index}") for index in range(1, 8)]
        plan = build_quarter_plan(context(tasks, dependencies=[
            {"blocking_task_id": f"T{index}", "blocked_task_id": f"T{index + 1}"}
            for index in range(1, 7)
        ]))
        item = explanation(plan, "T7")
        self.assertIn("QUARTER_OVERFLOW", {reason.code.value for reason in item.reasons})
        self.assertIn("MOVE_NEXT_QUARTER", {recommendation.code.value for recommendation in item.recommendations})


if __name__ == "__main__":
    unittest.main()
