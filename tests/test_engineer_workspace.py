from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import app, plan_drafts
from src.web.engineer_workspace import build_engineer_workspace


def approved_payload() -> dict:
    engineer = {
        "engineer_id": "ENG-1",
        "name": "Ирина",
        "roles": ["Backend"],
        "team_ids": ["Team-A", "Team-B"],
        "capacity_hours": 80,
        "capacity_buckets": [
            {"team_id": "Team-A", "capacity_rate": 0.75, "capacity_hours": 60},
            {"team_id": "Team-B", "capacity_rate": 0.25, "capacity_hours": 20},
        ],
    }
    task = {
        "task_id": "TASK-1",
        "title": "Основная задача",
        "rung": 90,
        "status": "PLANNED",
        "status_label": "Запланирована",
        "assigned_team_id": "Team-A",
        "completion_sprint": 2,
        "dependencies": {"incoming": [
            {"blocking_task_id": "BLOCK", "state": "ACTIVE", "satisfied": False},
            {"blocking_task_id": "DONE", "state": "HISTORICAL_DONE", "satisfied": True},
        ]},
        "allocations": [
            {"engineer_id": "ENG-1", "role": "Backend", "source_team_id": "Team-A", "assigned_task_team_id": "Team-A", "sprint": 1, "allocated_hours": 24, "cross_team": False},
            {"engineer_id": "ENG-1", "role": "Backend", "source_team_id": "Team-A", "assigned_task_team_id": "Team-A", "sprint": 2, "allocated_hours": 16, "cross_team": False},
        ],
    }
    cross = {
        "task_id": "TASK-2",
        "title": "Помощь платформе",
        "rung": 80,
        "status": "PLANNED",
        "status_label": "Запланирована",
        "assigned_team_id": "Team-Platform",
        "completion_sprint": 1,
        "dependencies": {"incoming": []},
        "allocations": [
            {"engineer_id": "ENG-1", "role": "Backend", "source_team_id": "Team-B", "assigned_task_team_id": "Team-Platform", "sprint": 1, "allocated_hours": 20, "cross_team": True},
        ],
    }
    other = {
        "task_id": "TEAM-TASK",
        "title": "Не персональная",
        "status": "PLANNED",
        "assigned_team_id": "Team-A",
        "completion_sprint": 1,
        "dependencies": {"incoming": []},
        "allocations": [{"engineer_id": "ENG-2", "role": "Backend", "source_team_id": "Team-A", "sprint": 1, "allocated_hours": 80, "cross_team": False}],
    }
    return {
        "draft": {"approved": True},
        "plan": {"quarter": {"current_sprint": 1, "sprint_count": 6, "plan_version": "approved-v1"}},
        "engineers": [engineer],
        "tasks": [task, cross, other],
    }


class EngineerWorkspaceProjectionTests(unittest.TestCase):
    def test_missing_approved_plan_does_not_fall_back_to_draft(self) -> None:
        result = build_engineer_workspace(None, "ENG-1", catalog_engineer={"engineer_id": "ENG-1"})
        self.assertEqual(result["status"], "NO_APPROVED_PLAN")
        self.assertFalse(result["approved_plan"])
        self.assertEqual(result["tasks"]["all"], [])

    def test_engineer_sees_only_personal_allocations_without_double_count(self) -> None:
        result = build_engineer_workspace(approved_payload(), "ENG-1")
        self.assertEqual({item["task_id"] for item in result["tasks"]["all"]}, {"TASK-1", "TASK-2"})
        self.assertNotIn("TEAM-TASK", {item["task_id"] for item in result["tasks"]["all"]})
        self.assertEqual(result["summary"]["planned_hours"], 44)
        self.assertEqual(result["summary"]["free_hours"], 36)
        self.assertEqual(result["summary"]["unique_task_count"], 2)

    def test_cross_team_and_capacity_buckets_stay_explicit(self) -> None:
        result = build_engineer_workspace(approved_payload(), "ENG-1")
        current = result["sprints"][0]
        self.assertEqual(current["own_team_hours"], 24)
        self.assertEqual(current["cross_team_hours"], 20)
        self.assertEqual(len(result["cross_team_assignments"]), 1)
        self.assertEqual(result["cross_team_assignments"][0]["owner_team_id"], "Team-Platform")
        self.assertEqual(
            {item["team_id"]: item["allocated_hours"] for item in current["capacity_buckets"]},
            {"Team-A": 24, "Team-B": 20},
        )

    def test_multi_sprint_is_continuation_and_unique_task_is_not_duplicated(self) -> None:
        result = build_engineer_workspace(approved_payload(), "ENG-1")
        task = next(item for item in result["tasks"]["all"] if item["task_id"] == "TASK-1")
        self.assertEqual(task["planned_sprints"], [1, 2])
        self.assertTrue(task["multi_sprint"])
        self.assertTrue(result["sprints"][1]["assignments"][0]["continuation"])
        self.assertEqual(result["summary"]["unique_task_count"], 2)

    def test_only_active_dependencies_block_personal_task(self) -> None:
        result = build_engineer_workspace(approved_payload(), "ENG-1")
        task = next(item for item in result["tasks"]["all"] if item["task_id"] == "TASK-1")
        self.assertEqual([item["blocking_task_id"] for item in task["dependencies"]], ["BLOCK"])
        self.assertEqual(result["summary"]["blocked_task_count"], 1)

    def test_approved_plan_without_engineer_assignments_has_distinct_empty_state(self) -> None:
        result = build_engineer_workspace(approved_payload(), "ENG-3", catalog_engineer={"engineer_id": "ENG-3", "name": "Антон"})
        self.assertEqual(result["status"], "NO_ASSIGNMENTS")
        self.assertTrue(result["approved_plan"])
        self.assertIn("нет назначений", result["message"])


class EngineerWorkspaceFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        cls.html = (frontend / "index.html").read_text(encoding="utf-8")
        cls.app = (frontend / "app.js").read_text(encoding="utf-8")
        cls.api = (frontend / "api.js").read_text(encoding="utf-8")

    def test_mode_switch_and_personal_pages_exist(self) -> None:
        self.assertIn('data-workspace-mode="manager"', self.html)
        self.assertIn('data-workspace-mode="engineer"', self.html)
        for page in ("engineer-sprint", "engineer-quarter", "engineer-tasks"):
            self.assertIn(f'id="page-{page}"', self.html)
            self.assertIn(f"'{page}'", self.app)

    def test_frontend_uses_read_only_workspace_api_and_existing_task_route(self) -> None:
        self.assertIn("getEngineerWorkspace", self.api)
        self.assertIn("data-open-task", self.app)
        self.assertIn("renderEngineerParticipation", self.app)
        self.assertNotIn("story_points", self.app.split("function renderEngineerSprint()", 1)[1].split("function renderEngineerQuarter()", 1)[0])

    def test_direct_engineer_route_selects_engineer_mode(self) -> None:
        self.assertIn("page.startsWith('engineer-') ? 'engineer'", self.app)
        self.assertIn("postcode-workspace-mode", self.app)


PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


@unittest.skipUnless((PROCESSED_DIR / "engineers.csv").is_file(), "processed dataset is absent")
class EngineerWorkspaceAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_approved = plan_drafts.approved()
        plan_drafts.reset()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        plan_drafts.reset()
        if self.previous_approved is not None:
            with plan_drafts._lock:
                plan_drafts._approved = self.previous_approved

    def test_endpoint_never_substitutes_default_or_draft_plan_for_approved(self) -> None:
        engineers = [item for item in self.client.get("/api/engineers").json() if not item.get("archived")]
        self.assertTrue(engineers)
        response = self.client.get(f"/api/engineer-workspace/{engineers[0]['engineer_id']}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "NO_APPROVED_PLAN")
        self.assertEqual(payload["tasks"]["all"], [])


if __name__ == "__main__":
    unittest.main()
