from __future__ import annotations

import hashlib
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web.api import app, plan_drafts
from src.web.constructor import (
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_plan,
    validate_constructor_scenario,
)
from src.web.recommendations import RecommendationDraft


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
RAW_WORKBOOK = PROJECT_ROOT / "data" / "raw" / "dataset.xlsx"


def scenario_payload(*, engineer_skills: list[str] | None = None) -> dict:
    return {
        "teams": [
            {
                "team_id": "Team-A",
                "name": "Scenario Team",
                "expected_velocity_sp": 20,
                "focus_factor": 0.8,
            }
        ],
        "engineers": [
            {
                "engineer_id": "ENG-1",
                "name": "Scenario Engineer",
                "assignments": [
                    {
                        "team_id": "Team-A",
                        "role": "Backend",
                        "grade": "Middle",
                        "capacity_rate": 1,
                    }
                ],
                "skills": engineer_skills or ["Python"],
            }
        ],
        "tasks": [
            {
                "task_id": "TASK-1",
                "title": "Scenario task",
                "initiative_id": "PRODF-1",
                "team_id": "Team-A",
                "status": "ToDo",
                "rung": 100,
                "story_points": 5,
                "role_requirements": [
                    {
                        "role": "Backend",
                        "required_hours": 40,
                        "skills": ["Python"],
                    }
                ],
                "depends_on": [],
            }
        ],
    }


class ConstructorDomainTests(unittest.TestCase):
    @staticmethod
    def cross_team_payload(task_count: int = 1) -> dict:
        return {
            "teams": [
                {"team_id": "Team-A", "name": "A", "expected_velocity_sp": 20, "focus_factor": 0.8},
                {"team_id": "Team-B", "name": "B", "expected_velocity_sp": 20, "focus_factor": 0.8},
            ],
            "engineers": [{
                "engineer_id": "ENG-B", "name": "Engineer B",
                "assignments": [{"team_id": "Team-B", "role": "Backend", "capacity_rate": 1}],
                "skills": [],
            }],
            "tasks": [{
                "task_id": f"TASK-{index}", "title": "Needs help", "initiative_id": f"I-{index}",
                "team_id": "Team-A", "status": "ToDo", "rung": 100 - index,
                "story_points": 1, "role_requirements": [{"role": "Backend", "required_hours": 8, "skills": []}],
                "depends_on": [],
            } for index in range(1, task_count + 1)],
        }

    def test_recommendations_simulate_cross_team_without_changing_base_plan(self) -> None:
        payload = self.cross_team_payload()
        base = build_constructor_plan(ConstructorScenario(**payload))
        self.assertEqual(base["tasks"][0]["status"], "MOVED")
        draft = RecommendationDraft(base)
        recommendation = next(item for item in draft.recommendations.values() if item["type"] == "CROSS_TEAM_HELP")
        self.assertEqual(recommendation["engineer_id"], "ENG-B")
        self.assertEqual(draft.base_plan["tasks"][0]["status"], "MOVED")

        draft.apply(recommendation["recommendation_id"])
        planned = draft.current_plan["tasks"][0]
        self.assertEqual(planned["status"], "PLANNED")
        self.assertTrue(any(row["cross_team"] for row in planned["allocations"]))
        draft.undo(recommendation["recommendation_id"])
        self.assertEqual(draft.current_plan["tasks"][0]["status"], "MOVED")

    def test_reject_hides_recommendation_and_backend_keeps_all_candidates(self) -> None:
        base = build_constructor_plan(ConstructorScenario(**self.cross_team_payload(6)))
        draft = RecommendationDraft(base)
        self.assertGreater(len(draft.recommendations), 5)
        recommendation_id = next(iter(draft.recommendations))
        draft.reject(recommendation_id)
        self.assertNotIn(recommendation_id, draft.recommendations)
        self.assertFalse(draft.accepted)

    def test_apply_all_uses_the_saved_optimized_shadow_scenario(self) -> None:
        base = build_constructor_plan(ConstructorScenario(**self.cross_team_payload(3)))
        draft = RecommendationDraft(base)
        self.assertTrue(draft.optimized_actions)
        self.assertEqual(draft.current_plan["tasks"][0]["status"], "MOVED")
        draft.apply_all()
        self.assertEqual(
            [task["status"] for task in draft.current_plan["tasks"]],
            ["PLANNED", "PLANNED", "PLANNED"],
        )
        self.assertEqual(len(draft.last_changed_tasks), 3)
        self.assertGreaterEqual(
            draft.last_changed_tasks[0]["rung"], draft.last_changed_tasks[-1]["rung"]
        )
        self.assertTrue(draft.accepted)

    def test_apply_returns_current_draft_without_waiting_for_optimizer(self) -> None:
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**self.cross_team_payload()))
        )
        recommendation_id = next(iter(draft.recommendations))
        with patch.object(draft, "refresh_optimization") as optimizer:
            draft.apply(recommendation_id)
        optimizer.assert_not_called()
        snapshot = draft.snapshot("draft-1")
        self.assertEqual(snapshot["optimization"]["status"], "ready")
        self.assertFalse(snapshot["optimization"]["needs_refresh"])
        self.assertEqual(snapshot["tasks"][0]["status"], "PLANNED")
        self.assertEqual(snapshot["optimization"]["changed_tasks"][0]["change"], "added_to_plan")

    def test_best_plan_is_immutable_after_apply_reject_and_undo(self) -> None:
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**self.cross_team_payload(3)))
        )
        best_fingerprint = draft.snapshot("draft-1")["optimization"]["best_plan_fingerprint"]
        first_id = next(iter(draft.recommendations))
        draft.apply(first_id)
        self.assertEqual(draft.snapshot("draft-1")["optimization"]["best_plan_fingerprint"], best_fingerprint)
        second_id = next(iter(draft.recommendations))
        draft.reject(second_id)
        self.assertEqual(draft.snapshot("draft-1")["optimization"]["best_plan_fingerprint"], best_fingerprint)
        draft.undo(first_id)
        self.assertEqual(draft.snapshot("draft-1")["optimization"]["best_plan_fingerprint"], best_fingerprint)

    def test_apply_all_selects_exact_saved_best_and_undo_reenables_it(self) -> None:
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**self.cross_team_payload(2)))
        )
        draft.apply_all()
        selected = draft.snapshot("draft-1")["optimization"]
        self.assertTrue(selected["best_selected"])
        self.assertEqual(selected["current_plan_fingerprint"], selected["best_plan_fingerprint"])
        draft.undo(draft.accepted[0]["change_id"])
        self.assertFalse(draft.snapshot("draft-1")["optimization"]["best_selected"])

    def test_cross_team_capacity_evidence_uses_source_team_bucket(self) -> None:
        before = {
            "tasks": [{
                "task_id": "OWN", "assigned_team_id": "Team-L", "status": "PLANNED",
                "allocations": [],
            }, {
                "task_id": "HELP", "assigned_team_id": "Team-P", "status": "MOVED",
                "rung": 9, "allocations": [],
            }],
            "engineers": [{"engineer_id": "E1", "name": "Engineer One"}],
        }
        after = deepcopy(before)
        after["tasks"][0]["allocations"] = [{
            "engineer_id": "E1", "role": "Backend", "source_team_id": "Team-L",
            "assigned_task_team_id": "Team-L", "sprint": 1, "allocated_hours": 8,
            "capacity_bucket_hours": 40, "cross_team": False,
        }]
        after["tasks"][1].update({"status": "PLANNED", "completion_sprint": 1})
        after["tasks"][1]["allocations"] = [{
            "engineer_id": "E1", "role": "Backend", "source_team_id": "Team-L",
            "assigned_task_team_id": "Team-P", "sprint": 1, "allocated_hours": 30,
            "capacity_bucket_hours": 40, "cross_team": True,
        }]
        evidence = RecommendationDraft(deepcopy(before))._cross_team_evidence(before, after, ["HELP"])[0]
        capacity = evidence["engineer_capacity"]
        self.assertEqual(capacity["capacity_hours"], 40)
        self.assertEqual(capacity["already_allocated_hours"], 8)
        self.assertEqual(capacity["recommended_hours"], 30)
        self.assertEqual(capacity["resulting_total_hours"], 38)
        self.assertEqual(capacity["remaining_hours"], 2)
        self.assertTrue(capacity["fits_capacity"])

    def test_overloaded_cross_team_change_is_not_a_recommendation(self) -> None:
        before = {"tasks": [{
            "task_id": "HELP", "assigned_team_id": "Team-P", "status": "MOVED",
            "rung": 9, "allocations": [],
        }], "engineers": [{"engineer_id": "E1", "name": "Engineer One"}]}
        after = deepcopy(before)
        after["tasks"][0].update({"status": "PLANNED", "completion_sprint": 1})
        after["tasks"][0]["allocations"] = [{
            "engineer_id": "E1", "role": "Backend", "source_team_id": "Team-L",
            "assigned_task_team_id": "Team-P", "sprint": 1, "allocated_hours": 45,
            "capacity_bucket_hours": 40, "cross_team": True,
        }]
        draft = RecommendationDraft(deepcopy(before))
        self.assertIsNone(draft._recommendation(
            {"type": "CROSS_TEAM_HELP", "task_id": "HELP"}, before, after
        ))

    def test_recommendations_order_task_changes_before_help_and_by_rung(self) -> None:
        draft = RecommendationDraft({"tasks": []})
        draft._all_recommendations = {
            "help": {"recommendation_id": "help", "type": "CROSS_TEAM_HELP", "rung": 100, "task_id": "H", "effect": {}},
            "low": {"recommendation_id": "low", "type": "TASK_REASSIGNMENT", "rung": 5, "task_id": "L", "effect": {}},
            "high": {"recommendation_id": "high", "type": "TASK_REASSIGNMENT", "rung": 10, "task_id": "A", "effect": {}},
        }
        draft._refresh_available()
        ordered = list(draft.recommendations.values())
        self.assertEqual([item["recommendation_id"] for item in ordered], ["high", "low", "help"])

    def test_reassignment_recommendation_has_before_after_resource_evidence(self) -> None:
        payload = self.cross_team_payload()
        payload["engineers"][0]["assignments"][0]["capacity_rate"] = 0.5
        base = build_constructor_plan(ConstructorScenario(**payload))
        draft = RecommendationDraft(base)
        action = {
            "type": "TASK_REASSIGNMENT", "task_id": "TASK-1",
            "team_id": "Team-B", "source_team_id": "Team-A",
        }
        recommendation = draft._recommendation(action, draft.base_plan, draft._plan([action]))
        self.assertIsNotNone(recommendation)
        evidence = recommendation["task_reassignment"][0]
        self.assertEqual(evidence["from_team"], "Team-A")
        self.assertEqual(evidence["to_team"], "Team-B")
        self.assertIn("role_hours_required", evidence["target_capacity"])
        self.assertIn("role_hours_available", evidence["target_capacity"])
        self.assertNotIn("before_kpi", recommendation["effect"])

    def test_reciprocal_reassignments_are_one_atomic_swap(self) -> None:
        base = {"tasks": [
            {"task_id": "A", "assigned_team_id": "Team-A", "rung": 10, "allocations": []},
            {"task_id": "B", "assigned_team_id": "Team-B", "rung": 9, "allocations": []},
        ]}
        shadow = {"tasks": [
            {"task_id": "A", "assigned_team_id": "Team-B", "allocations": []},
            {"task_id": "B", "assigned_team_id": "Team-A", "allocations": []},
        ]}
        draft = RecommendationDraft(deepcopy(base))
        actions = draft._shadow_actions(shadow)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "TASK_SWAP")
        self.assertEqual({item["task_id"] for item in actions[0]["changes"]}, {"A", "B"})

    def test_refresh_rejects_a_stale_draft_revision(self) -> None:
        draft = RecommendationDraft(
            build_constructor_plan(ConstructorScenario(**self.cross_team_payload()))
        )
        with self.assertRaisesRegex(ValueError, "stale revision"):
            draft.refresh_optimization(expected_revision=draft.revision + 1)

    def test_changed_task_dto_reports_move_and_cross_team_update(self) -> None:
        before = {"tasks": [{
            "task_id": "TASK-A", "status": "PLANNED", "completion_sprint": 5,
            "assigned_team_id": "Team-A", "rung": 10, "allocations": [],
        }]}
        after = {"tasks": [{
            "task_id": "TASK-A", "status": "PLANNED", "completion_sprint": 3,
            "assigned_team_id": "Team-A", "rung": 10,
            "allocations": [{"cross_team": True}],
        }]}
        change = RecommendationDraft._changed_tasks(before, after)[0]
        self.assertEqual(change["change"], "moved")
        self.assertEqual(change["from_sprint"], 5)
        self.assertEqual(change["to_sprint"], 3)

    def test_human_reason_blocks_group_duplicate_roles(self) -> None:
        from src.web.service import _human_reason_blocks
        blocks = _human_reason_blocks([
            {"code": "MISSING_SPECIALIST", "details": {"role": "Backend"}},
            {"code": "MISSING_SPECIALIST", "details": {"role": "QA"}},
            {"code": "MISSING_SPECIALIST", "details": {"role": "Backend"}},
            {"code": "DEPENDENCY_NOT_COMPLETED", "details": {"blocking_task_id": "TASK-0"}},
        ])
        self.assertEqual([block["type"] for block in blocks], ["dependency", "specialists"])
        self.assertEqual(blocks[1]["details"], ["Backend", "QA"])

    def test_real_planner_builds_six_sprints_from_ephemeral_scenario(self) -> None:
        result = build_constructor_plan(ConstructorScenario(**scenario_payload()))

        self.assertEqual(len(result["plan"]["sprints"]), 6)
        self.assertEqual(result["tasks"][0]["status"], "PLANNED")
        self.assertEqual(result["tasks"][0]["allocations"][0]["engineer_id"], "ENG-1")
        self.assertEqual(result["teams"][0]["name"], "Scenario Team")
        self.assertEqual(result["engineers"][0]["name"], "Scenario Engineer")
        self.assertFalse(result["draft"]["server_persisted"])
        self.assertFalse(result["draft"]["source_dataset_modified"])

    def test_required_skill_is_a_hard_constraint(self) -> None:
        payload = scenario_payload(engineer_skills=["Go"])
        result = build_constructor_plan(ConstructorScenario(**payload))

        task = result["tasks"][0]
        self.assertEqual(task["status"], "MOVED")
        self.assertIn("INSUFFICIENT_SKILL_CAPACITY", {item["code"] for item in task["reasons"]})
        self.assertEqual(task["allocations"], [])

    def test_planner_assigns_team_when_task_has_no_source_team(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["team_id"] = None

        result = build_constructor_plan(ConstructorScenario(**payload))

        task = result["tasks"][0]
        self.assertEqual(task["status"], "PLANNED")
        self.assertIsNone(task["original_team_id"])
        self.assertEqual(task["assigned_team_id"], "Team-A")

    def test_unknown_dependency_and_cycle_are_rejected(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["depends_on"] = ["MISSING"]
        with self.assertRaisesRegex(ScenarioValidationError, "MISSING"):
            validate_constructor_scenario(ConstructorScenario(**payload))

        payload = scenario_payload()
        payload["tasks"].append(
            {
                **payload["tasks"][0],
                "task_id": "TASK-2",
                "title": "Second task",
                "depends_on": ["TASK-1"],
            }
        )
        payload["tasks"][0]["depends_on"] = ["TASK-2"]
        with self.assertRaisesRegex(ScenarioValidationError, "цикл"):
            validate_constructor_scenario(ConstructorScenario(**payload))

    def test_non_standard_focus_factor_is_rejected(self) -> None:
        payload = scenario_payload()
        payload["teams"][0]["focus_factor"] = 1
        with self.assertRaisesRegex(ScenarioValidationError, "0.8"):
            validate_constructor_scenario(ConstructorScenario(**payload))

    def test_engineer_capacity_is_not_duplicated_between_teams(self) -> None:
        payload = scenario_payload()
        payload["teams"].append(
            {"team_id": "Team-B", "expected_velocity_sp": 20, "focus_factor": 0.8}
        )
        payload["engineers"][0]["assignments"] = [
            {"team_id": "Team-A", "role": "Backend", "capacity_rate": 0.6},
            {"team_id": "Team-B", "role": "Backend", "capacity_rate": 0.6},
        ]

        with self.assertRaisesRegex(ScenarioValidationError, "Capacity Rate"):
            validate_constructor_scenario(ConstructorScenario(**payload))


@unittest.skipUnless((PROCESSED_DIR / "tasks.csv").is_file(), "processed dataset is absent")
class ConstructorAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        plan_drafts.reset()

    @classmethod
    def tearDownClass(cls) -> None:
        plan_drafts.reset()

    def test_catalog_exposes_editable_copies_of_dataset_entities(self) -> None:
        response = self.client.get("/api/plan-constructor/catalog")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["source_dataset_read_only"])
        self.assertTrue(payload["teams"])
        self.assertTrue(payload["engineers"])
        self.assertTrue(payload["tasks"])
        self.assertEqual(payload["options"]["focus_factor"], 0.8)

    def test_plan_endpoint_does_not_modify_processed_dataset(self) -> None:
        files = sorted(PROCESSED_DIR.glob("*"))
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
            if path.is_file()
        }

        response = self.client.post(
            "/api/plan-constructor/plan", json=scenario_payload()
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["plan"]["sprints"]), 6)
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_build_keeps_current_plan_until_the_reviewed_draft_is_approved(self) -> None:
        current_before = self.client.get("/api/dashboard")
        self.assertEqual(current_before.status_code, 200)

        draft_response = self.client.post(
            "/api/plan-constructor/plan", json=scenario_payload()
        )
        self.assertEqual(draft_response.status_code, 200, draft_response.text)
        draft = draft_response.json()
        self.assertFalse(draft["draft"]["approved"])

        current_after_build = self.client.get("/api/dashboard")
        self.assertEqual(current_after_build.status_code, 200)
        self.assertEqual(current_after_build.json()["plan"], current_before.json()["plan"])

        approval = self.client.post(
            "/api/plan-constructor/drafts/approve",
            json={"draft_id": draft["draft"]["id"]},
        )
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertTrue(approval.json()["draft"]["approved"])

        current_after_approval = self.client.get("/api/dashboard")
        self.assertEqual(current_after_approval.status_code, 200)
        self.assertEqual(
            current_after_approval.json()["plan"]["quarter"]["plan_version"],
            "constructor-base-draft",
        )
        self.assertEqual(
            [task["task_id"] for task in current_after_approval.json()["tasks"]],
            ["TASK-1"],
        )

    def test_apply_recommendation_keeps_current_plan_until_exact_draft_is_approved(self) -> None:
        plan_drafts.reset()
        response = self.client.post(
            "/api/plan-constructor/plan",
            json=ConstructorDomainTests.cross_team_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        recommendation = next(
            item for item in draft["optimization"]["recommendations"]
            if item["type"] == "CROSS_TEAM_HELP"
        )
        applied = self.client.post(
            f"/api/plan-constructor/drafts/{draft['draft']['id']}/recommendations/apply",
            json={"recommendation_id": recommendation["recommendation_id"]},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["optimization"]["status"], "ready")
        self.assertFalse(applied.json()["optimization"]["needs_refresh"])
        revision = applied.json()["optimization"]["draft_revision"]
        refreshed = self.client.post(
            f"/api/plan-constructor/drafts/{draft['draft']['id']}/recommendations/refresh",
            json={"revision": revision},
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json()["optimization"]["recommendations_revision"], revision)
        current_before_approval = self.client.get("/api/dashboard").json()
        self.assertNotEqual(current_before_approval["tasks"][0]["task_id"], "TASK-1")
        approval = self.client.post(
            "/api/plan-constructor/drafts/approve", json={"draft_id": draft["draft"]["id"]},
        )
        self.assertEqual(approval.status_code, 200, approval.text)
        self.assertEqual(approval.json()["plan"], applied.json()["plan"])
        self.assertTrue(approval.json()["tasks"][0]["allocations"][0]["cross_team"])

    def test_apply_all_changes_only_draft_until_approve(self) -> None:
        plan_drafts.reset()
        built = self.client.post("/api/plan-constructor/plan", json=ConstructorDomainTests.cross_team_payload())
        self.assertEqual(built.status_code, 200, built.text)
        draft = built.json()
        current_before = self.client.get("/api/dashboard").json()
        applied = self.client.post(f"/api/plan-constructor/drafts/{draft['draft']['id']}/recommendations/apply-all")
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()["tasks"][0]["status"], "PLANNED")
        self.assertEqual(self.client.get("/api/dashboard").json()["plan"], current_before["plan"])

    @unittest.skipUnless(RAW_WORKBOOK.is_file(), "raw workbook is absent")
    def test_uploaded_workbook_builds_plan_without_modifying_project_data(self) -> None:
        protected_files = [RAW_WORKBOOK, *sorted(PROCESSED_DIR.glob("*"))]
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in protected_files
            if path.is_file()
        }
        current_before = self.client.get("/api/dashboard")
        self.assertEqual(current_before.status_code, 200)

        response = self.client.post(
            "/api/plan-constructor/plan/upload",
            params={"filename": RAW_WORKBOOK.name},
            content=RAW_WORKBOOK.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["uploaded_dataset"]["name"], RAW_WORKBOOK.name)
        self.assertTrue(payload["uploaded_dataset"]["server_persisted"])
        self.assertIn("catalog_merge", payload["uploaded_dataset"])
        self.assertEqual(len(payload["plan"]["sprints"]), 6)
        self.assertTrue(payload["teams"])
        self.assertTrue(payload["engineers"])
        self.assertTrue(payload["tasks"])
        current_after_build = self.client.get("/api/dashboard")
        self.assertEqual(current_after_build.status_code, 200)
        self.assertEqual(current_after_build.json()["plan"], current_before.json()["plan"])
        after = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in protected_files
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_upload_endpoint_rejects_non_excel_content(self) -> None:
        response = self.client.post(
            "/api/plan-constructor/plan/upload",
            params={"filename": "dataset.csv"},
            content=b"not an Excel workbook",
            headers={"Content-Type": "application/octet-stream"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(".xlsx", response.json()["detail"]["errors"][0])

    def test_upload_endpoint_rejects_broken_workbook(self) -> None:
        response = self.client.post(
            "/api/plan-constructor/plan/upload",
            params={"filename": "broken.xlsx"},
            content=b"not an Excel workbook",
            headers={"Content-Type": "application/octet-stream"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(
            "Не удалось использовать",
            response.json()["detail"]["message"],
        )

    def test_plan_endpoint_returns_readable_cross_entity_errors(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["team_id"] = "UNKNOWN"

        response = self.client.post("/api/plan-constructor/plan", json=payload)

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIn("ошибки", detail["message"])
        self.assertTrue(any("UNKNOWN" in item for item in detail["errors"]))


if __name__ == "__main__":
    unittest.main()
