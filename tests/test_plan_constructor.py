from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import app, plan_drafts
from src.web.constructor import (
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_plan,
    validate_constructor_scenario,
)


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
            "constructor-draft",
        )
        self.assertEqual(
            [task["task_id"] for task in current_after_approval.json()["tasks"]],
            ["TASK-1"],
        )

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
