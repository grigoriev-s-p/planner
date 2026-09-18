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
        for page_id in (
            "home", "planning", "current-plan", "tasks", "risks", "recalc", "star"
        ):
            self.assertIn(f'id="page-{page_id}"', page.text)
        for asset in ("/styles.css", "/api.js", "/constructor.js", "/app.js"):
            self.assertEqual(self.client.get(asset).status_code, 200)
        self.assertNotIn("СЦЕНАРИЙ / 6 СПРИНТОВ", page.text)
        self.assertNotIn("Исходный датасет только для чтения", page.text)
        self.assertNotIn("Черновик готов", page.text)
        self.assertNotIn('id="addConstructorEngineer"', page.text)
        self.assertNotIn('id="constructorTaskTeam"', page.text)
        self.assertIn('id="constructorTaskSearch"', page.text)
        self.assertIn('id="constructorDatasetInput"', page.text)
        self.assertIn('id="uploadConstructorDataset"', page.text)
        self.assertIn('data-constructor-mode="manual"', page.text)
        self.assertIn('data-constructor-mode="upload"', page.text)
        self.assertNotIn('id="constructorAutoResult"', page.text)

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
        bus_factor = star_map.json()
        self.assertEqual(bus_factor["status"], "AVAILABLE")
        self.assertEqual(bus_factor["method"], "existing_bus_factor_service")
        self.assertTrue(bus_factor["teams"])
        first_competency = next(
            competency
            for team in bus_factor["teams"]
            for competency in team["competencies"]
        )
        engineer_ids = [
            engineer["engineer_id"] for engineer in first_competency["engineers"]
        ]
        self.assertEqual(first_competency["bus_factor"], len(engineer_ids))
        self.assertEqual(len(engineer_ids), len(set(engineer_ids)))

    def test_dashboard_explicitly_reports_unimplemented_features(self) -> None:
        self.assertEqual(self.dashboard["features"]["replanning"], "NOT_IMPLEMENTED")
        self.assertEqual(self.dashboard["features"]["bus_factor"], "AVAILABLE")
        self.assertEqual(
            self.dashboard["features"]["initial_excel_upload"],
            "AVAILABLE_PERSISTENT_CATALOG",
        )

    def test_kpi_endpoint_exposes_structured_unavailable_fact_without_internal_ids(self) -> None:
        response = self.client.get("/api/kpis")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, self.dashboard["kpis"])
        self.assertNotIn("baseline_id", json.dumps(payload))
        self.assertNotIn("plan_version", json.dumps(payload))

        expected = payload["expected_kpi"]
        self.assertEqual(expected["completed_initiatives"], 2)
        self.assertEqual(expected["baseline_initiatives"], 7)
        self.assertEqual(expected["incomplete_initiatives"], 5)
        self.assertAlmostEqual(expected["completion_percent"], 2 / 7 * 100)

        quarter = payload["quarter_kpi"]
        self.assertFalse(quarter["has_actual"])
        self.assertIsNone(quarter["completed_initiatives"])
        self.assertIsNone(quarter["completion_percent"])
        self.assertGreater(quarter["baseline_initiatives"], 0)

        sprints = payload["sprint_kpis"]
        self.assertEqual([item["sprint"] for item in sprints], list(range(1, 7)))
        self.assertTrue(any(item["baseline_sp"] > 0 for item in sprints))
        for item in sprints:
            self.assertFalse(item["has_actual"])
            self.assertIsNone(item["actual_sp"])
            self.assertIsNone(item["completion_percent"])

    def test_frontend_uses_central_api_client_without_demo_business_arrays(self) -> None:
        frontend = PROCESSED_DIR.parents[1] / "frontend"
        api_source = (frontend / "api.js").read_text(encoding="utf-8")
        app_source = (frontend / "app.js").read_text(encoding="utf-8")
        constructor_source = (frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertEqual(api_source.count("fetch("), 1)
        self.assertNotIn("fetch(", app_source)
        self.assertNotIn("fetch(", constructor_source)
        self.assertIn("escapeHtml", app_source)
        self.assertIn("localStorage", constructor_source)
        self.assertIn("buildConstructorPlan", constructor_source)
        self.assertIn("buildConstructorPlanFromDataset", constructor_source)
        self.assertIn("previewPlan = planResult", constructor_source)
        self.assertIn("approveConstructorPlan", constructor_source)
        self.assertIn("invalidatePreview", constructor_source)
        self.assertIn("Помощь между командами", constructor_source)
        self.assertIn("Переносы и исключения", constructor_source)
        self.assertIn("hydrate(plannerResult)", app_source)
        self.assertIn("show('current-plan')", app_source)
        self.assertIn("automaticEngineers", constructor_source)
        self.assertIn("catalogWithDraft('tasks')", constructor_source)
        self.assertIn("Number(right.expected_velocity_sp || 0)", constructor_source)
        self.assertIn('team_id: null', constructor_source)
        self.assertIn('list="constructorInitiativeOptions"', constructor_source)
        self.assertIn('name="dependency_id"', constructor_source)
        self.assertIn("/api/plan-constructor/plan", api_source)
        self.assertIn("/api/plan-constructor/plan/upload", api_source)
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
        self.assertNotIn('data-page="constructor"', navigation)
        for page in ("home", "planning", "current-plan", "risks", "recalc", "star"):
            self.assertIn(f'data-page="{page}"', navigation)
        self.assertIn('data-page="planning">Планирование</button>', navigation)
        self.assertIn('data-page="current-plan">Текущий план</button>', navigation)

    def test_constructor_approves_preview_before_replacing_current_plan(self) -> None:
        self.assertIn("planning: window.planConstructor.render", self.app)
        self.assertIn("'current-plan': renderPlanning", self.app)
        self.assertIn("hydrate(plannerResult);", self.app)
        self.assertIn("show('current-plan');", self.app)
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("previewPlan = planResult", constructor_source)
        self.assertIn("approveConstructorPlan(previewPlan.draft.id)", constructor_source)
        self.assertIn("onPlanApproved(approvedPlan);", constructor_source)

    def test_constructor_kpi_uses_backend_percent_without_second_multiplication(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("format(parsed)", constructor_source)
        self.assertNotIn("Number(value) * 100", constructor_source)
        self.assertNotIn("Math.round(expectedKpi * 100)", constructor_source)
        self.assertIn("KPI недоступен", constructor_source)

    def test_constructor_renders_changed_task_effect_without_reoptimizing(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("changed_tasks", constructor_source)
        self.assertIn("scrollIntoView", constructor_source)
        self.assertIn("task-card--just-added", constructor_source)
        self.assertIn("prefers-reduced-motion", constructor_source)
        self.assertIn("applyOptimisticDelta", constructor_source)
        self.assertNotIn("refreshRecommendations(draftId", constructor_source)

    def test_recommendation_ui_uses_work_volume_and_saved_best_state(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("Лучший найденный вариант выбран", constructor_source)
        self.assertIn("Перераспределение задач", constructor_source)
        self.assertIn("Помощь между командами", constructor_source)
        self.assertIn("После подключения", constructor_source)
        self.assertNotIn("KPI: ${percent(effect.before_kpi)}", constructor_source)

    def test_home_plan_links_open_current_plan(self) -> None:
        home = self.html.split('id="page-home"', 1)[1].split('id="page-planning"', 1)[0]
        self.assertIn('data-page="current-plan"', home)
        self.assertNotIn('data-page="planning"', home)

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
        self.assertIn("taskReturnPage: 'current-plan'", self.app)
        self.assertNotIn('id="taskSearch"', self.html)

    def test_development_notice_is_reused_on_unfinished_pages(self) -> None:
        self.assertEqual(self.app.count("renderDevelopmentNotice('#"), 2)
        for target in (
            "risksDevelopmentNotice",
            "recalcDevelopmentNotice",
        ):
            self.assertIn(f'id="{target}"', self.html)

    def test_star_map_uses_backend_bus_factor_for_selected_team(self) -> None:
        self.assertIn('id="competencyMap"', self.html)
        self.assertIn('id="busRiskList"', self.html)
        self.assertIn('id="competencyDetail"', self.html)
        self.assertIn("const busTeam = starTeamBy(state.teamId)", self.app)
        self.assertIn("team.competencies.map", self.app)
        self.assertIn("selected.engineers.map", self.app)
        self.assertIn("busProfile?.team_criticality", self.app)
        self.assertNotIn("Backend пока не рассчитывает Bus Factor", self.html + self.app)

    def test_kpi_block_follows_board_and_precedes_initiatives(self) -> None:
        board_position = self.html.index('id="sprintBoard"')
        kpi_position = self.html.index('id="planKpis"')
        initiatives_position = self.html.index('class="initiative-grid"')
        self.assertLess(board_position, kpi_position)
        self.assertLess(kpi_position, initiatives_position)
        self.assertIn("Ожидаемый KPI плана", self.html)

    def test_frontend_only_formats_backend_kpi_values(self) -> None:
        render_kpis = self.app.split("function renderKpis()", 1)[1].split(
            "function renderPlanning()", 1
        )[0]
        self.assertIn("expected.completion_percent", render_kpis)
        self.assertIn("expected.completed_initiatives", render_kpis)
        self.assertIn("expected.baseline_initiatives", render_kpis)
        self.assertNotIn("sprint_kpis", render_kpis)
        self.assertNotIn("Факт ещё не загружен", render_kpis)
        self.assertNotIn("Sprint", render_kpis)
        self.assertNotIn("baseline_id", self.html + render_kpis)
        planning_html = self.html.split('id="page-current-plan"', 1)[1].split(
            'id="page-tasks"', 1
        )[0]
        self.assertNotIn("ПРОГНОЗ", planning_html)
        self.assertNotIn("Прогноз", planning_html)

    def test_archive_modal_requires_structured_reason_and_other_text(self) -> None:
        api = (self.frontend / "api.js").read_text(encoding="utf-8")
        self.assertIn('id="archiveDialog"', self.html)
        self.assertIn('id="archiveReason" required', self.html)
        self.assertIn('id="archiveReasonText"', self.html)
        self.assertIn('id="confirmArchive" type="submit" disabled', self.html)
        self.assertIn("reasonCode === 'OTHER'", self.app)
        self.assertIn("!$('#archiveReasonText').value.trim()", self.app)
        self.assertIn("reason_code: reasonCode", self.app)
        self.assertIn("archiveEntity:", api)
        self.assertIn("/archive`,", api)

    def test_entity_cards_offer_archive_and_show_lifecycle(self) -> None:
        for entity_type in ("task", "engineer", "team"):
            self.assertIn(f'data-archive-entity="{entity_type}"', self.app)
        self.assertIn("archiveState(task)", self.app)
        self.assertIn("archiveState(engineer)", self.app)
        self.assertIn("archiveState(team)", self.app)
        self.assertIn("текущий план останется без изменений", self.app.lower())

    def test_historical_done_dependency_is_rendered_as_satisfied(self) -> None:
        self.assertIn("item.state === 'HISTORICAL_DONE'", self.app)
        self.assertIn("dependency-satisfied", self.app)
        self.assertIn("item.status_message", self.app)


if __name__ == "__main__":
    unittest.main()
