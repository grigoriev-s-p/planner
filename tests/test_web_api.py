from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import app


PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


@unittest.skipUnless((PROCESSED_DIR / "tasks.csv").is_file(), "processed dataset is absent")
class ProductAPIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        response = cls.client.get("/api/dashboard")
        assert response.status_code == 200, response.text
        cls.dashboard = response.json()

    def test_frontend_and_assets_are_served(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        for page_id in ("home", "planning", "tasks", "risks", "recalc", "star"):
            self.assertIn(f'id="page-{page_id}"', page.text)
        for asset in ("/styles.css", "/api.js", "/app.js"):
            self.assertEqual(self.client.get(asset).status_code, 200)

    def test_plan_contract_is_json_serializable_and_preserves_source_ids(self) -> None:
        response = self.client.get("/api/plan")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        json.dumps(payload)
        self.assertEqual(payload["quarter"]["sprint_count"], 6)
        self.assertEqual(len(payload["sprints"]), 6)

        tasks = payload["tasks"]
        self.assertTrue(tasks)
        with (PROCESSED_DIR / "tasks.csv").open(encoding="utf-8-sig", newline="") as source:
            source_ids = {row["task_id"] for row in csv.DictReader(source)}
        self.assertEqual({task["task_id"] for task in tasks}, source_ids)
        self.assertTrue(any(task["initiative_id"] for task in tasks))
        self.assertTrue(any(task["multi_sprint"] for task in tasks))

    def test_task_contract_contains_planner_explanations_and_allocations(self) -> None:
        tasks = self.dashboard["tasks"]
        explained = [task for task in tasks if task["reasons"]]
        recommended = [task for task in tasks if task["recommendations"]]
        cross_team = [
            allocation
            for task in tasks
            for allocation in task["allocations"]
            if allocation["cross_team"]
        ]
        self.assertTrue(explained)
        self.assertTrue(recommended)
        self.assertTrue(cross_team)
        self.assertIn("capacity_used_share", cross_team[0])
        self.assertIn("original_team_id", tasks[0])
        self.assertIn("assigned_team_id", tasks[0])
        self.assertTrue(any(task["team_reassigned"] for task in tasks))

        task_id = tasks[0]["task_id"]
        response = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], task_id)

    def test_optional_nulls_and_unknown_entities_are_safe(self) -> None:
        payload = self.client.get("/api/plan").json()
        self.assertTrue(any(task["forecast_start_date"] is None for task in payload["tasks"]))
        self.assertEqual(self.client.get("/api/tasks/UNKNOWN-NOT-A-TASK").status_code, 404)
        self.assertEqual(self.client.get("/api/teams/UNKNOWN-NOT-A-TEAM").status_code, 404)
        self.assertEqual(self.client.get("/api/engineers/UNKNOWN-NOT-AN-ENGINEER").status_code, 404)

    def test_risks_teams_engineers_and_star_map_are_backend_data(self) -> None:
        risks = self.client.get("/api/risks")
        teams = self.client.get("/api/teams")
        engineers = self.client.get("/api/engineers")
        star_map = self.client.get("/api/star-map")
        for response in (risks, teams, engineers, star_map):
            self.assertEqual(response.status_code, 200)
            json.dumps(response.json())

        self.assertTrue(risks.json())
        self.assertTrue(teams.json())
        self.assertTrue(engineers.json())
        self.assertEqual(
            len({engineer["engineer_id"] for engineer in engineers.json()}),
            len(engineers.json()),
        )
        self.assertIsNone(star_map.json()["bus_factor"])
        self.assertEqual(star_map.json()["bus_factor_status"], "NOT_IMPLEMENTED")

    def test_dashboard_explicitly_reports_unimplemented_features(self) -> None:
        self.assertEqual(self.dashboard["features"]["replanning"], "NOT_IMPLEMENTED")
        self.assertEqual(self.dashboard["features"]["bus_factor"], "NOT_IMPLEMENTED")

    def test_frontend_uses_central_api_client_without_demo_business_arrays(self) -> None:
        frontend = PROCESSED_DIR.parents[1] / "frontend"
        api_source = (frontend / "api.js").read_text(encoding="utf-8")
        app_source = (frontend / "app.js").read_text(encoding="utf-8")
        self.assertEqual(api_source.count("fetch("), 1)
        self.assertNotIn("fetch(", app_source)
        self.assertIn("escapeHtml", app_source)
        for old_demo_name in (
            "sampleTasks",
            "riskRecords",
            "initiativeNames",
            "demoChanges",
            "roleResources",
        ):
            self.assertNotIn(old_demo_name, app_source)


class FrontendRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frontend = PROCESSED_DIR.parents[1] / "frontend"
        cls.html = (cls.frontend / "index.html").read_text(encoding="utf-8")
        cls.app = (cls.frontend / "app.js").read_text(encoding="utf-8")
        cls.styles = (cls.frontend / "styles.css").read_text(encoding="utf-8")

    def test_top_navigation_has_no_tasks_entry(self) -> None:
        navigation = self.html.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn('data-page="tasks"', navigation)
        for page in ("home", "planning", "risks", "recalc", "star"):
            self.assertIn(f'data-page="{page}"', navigation)

    def test_technical_banner_and_header_version_are_absent(self) -> None:
        self.assertNotIn("dataModeRibbon", self.html + self.app)
        self.assertNotIn("header-quarter", self.html + self.app)
        self.assertNotIn("РЕАЛЬНЫЕ ДАННЫЕ", self.html + self.app)
        self.assertNotIn("Processed dataset", self.html + self.app)

    def test_home_uses_current_planned_sprints_instead_of_source_status(self) -> None:
        render_home = self.app.split("function renderHome()", 1)[1].split(
            "function initiativeTaskList", 1
        )[0]
        self.assertIn("inQuarter(task) && taskSprints(task).includes(currentSprint())", render_home)
        self.assertNotIn("source_status", render_home)
        self.assertNotIn("active.slice", render_home)

    def test_board_renders_six_compact_columns_without_global_horizontal_scroll(self) -> None:
        render_planning = self.app.split("function renderPlanning()", 1)[1].split(
            "function renderTasks()", 1
        )[0]
        self.assertIn("Array.from({length: 6}", render_planning)
        self.assertIn("sprint-task-scroll", render_planning)
        self.assertIn("grid-template-columns:repeat(6,minmax(0,1fr))", self.styles)
        self.assertIn(".board-viewport{overflow:hidden", self.styles)
        for forbidden in ("priority_label", "status_label", "initiative_id", "portion(task, sprint)"):
            self.assertNotIn(forbidden, render_planning)

    def test_initiatives_and_excluded_tasks_use_backend_results(self) -> None:
        self.assertIn('id="completedInitiatives"', self.html)
        self.assertIn('id="incompleteInitiatives"', self.html)
        self.assertIn("initiative.completed_in_quarter", self.app)
        self.assertIn("task.reasons", self.app)
        self.assertIn("REASON_GROUPS", self.app)
        self.assertIn("Другая причина", self.app)

    def test_task_detail_is_contextual_and_has_dynamic_hero_id(self) -> None:
        self.assertIn('id="page-tasks"', self.html)
        self.assertIn('id="taskHeroId"', self.html)
        self.assertIn("$('#taskHeroId').textContent = task.task_id", self.app)
        self.assertIn("taskReturnPage", self.app)
        self.assertNotIn('id="taskSearch"', self.html)

    def test_development_notice_is_reused_on_three_pages(self) -> None:
        self.assertEqual(self.app.count("renderDevelopmentNotice('#"), 3)
        for target in (
            "risksDevelopmentNotice",
            "recalcDevelopmentNotice",
            "starDevelopmentNotice",
        ):
            self.assertIn(f'id="{target}"', self.html)


if __name__ == "__main__":
    unittest.main()
