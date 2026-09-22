from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import app
from src.web.service import PlanningAPIService
from tests.acceptance.builders import SyntheticQuarter


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
            "planning", "current-plan", "tasks", "task-detail", "risks", "recalc"
        ):
            self.assertIn(f'id="page-{page_id}"', page.text)
        self.assertNotIn('id="page-home"', page.text)
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

    def test_current_plan_contract_excludes_historical_done_and_preserves_active_source_ids(self) -> None:
        response = self.client.get("/api/plan")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        json.dumps(payload)
        self.assertEqual(payload["quarter"]["sprint_count"], 6)
        self.assertEqual(len(payload["sprints"]), 6)

        tasks = payload["tasks"]
        self.assertTrue(tasks)
        with (PROCESSED_DIR / "tasks.csv").open(encoding="utf-8-sig", newline="") as source:
            active_source_ids = {
                row["task_id"] for row in csv.DictReader(source)
                if row["status"].strip().casefold() != "done"
            }
        self.assertEqual({task["task_id"] for task in tasks}, active_source_ids)
        self.assertFalse(any(task["status"] == "DONE" for task in tasks))
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
        planned = [task for task in tasks if task["status"] == "PLANNED"]
        self.assertTrue(all(task["ownership"]["valid"] for task in planned))
        for task in planned:
            ownership = task["ownership"]
            if ownership["external_hours"] > 0:
                self.assertGreater(ownership["owner_hours"], ownership["external_hours"])
                self.assertGreaterEqual(
                    ownership["owner_engineer_count"],
                    ownership["external_engineer_count"],
                )

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

        risk_rows = risks.json()
        self.assertTrue(risk_rows)
        self.assertTrue(teams.json())
        self.assertTrue(engineers.json())
        self.assertTrue({"ORANGE", "YELLOW"}.issubset({risk["severity"] for risk in risk_rows}))
        self.assertTrue(all(risk["severity"] in {"RED", "ORANGE", "YELLOW"} for risk in risk_rows))
        self.assertTrue(all(risk["effect"] for risk in risk_rows))
        self.assertTrue(all(risk["recommendation"] for risk in risk_rows))
        self.assertTrue(all(risk["evidence"] for risk in risk_rows))
        self.assertFalse({"MULTI_SPRINT", "CROSS_TEAM_DEPENDENCY"} & {risk["code"] for risk in risk_rows})
        orange = next(risk for risk in risk_rows if risk["severity"] == "ORANGE")
        labels = {item["label"] for item in orange["evidence"]}
        self.assertTrue({"Роль", "Требуется", "Доступно", "Дефицит"}.issubset(labels))
        self.assertIn("bus_factor_context", orange)
        yellow = next(risk for risk in risk_rows if risk["severity"] == "YELLOW")
        self.assertIn("в исходном плане", yellow["cause"].lower())
        self.assertIn("один спринт запаса", yellow["cause"].lower())
        self.assertIn("при replanning", yellow["cause"].lower())
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

    def test_dashboard_reports_available_product_features(self) -> None:
        self.assertEqual(self.dashboard["features"]["replanning"], "AVAILABLE")
        self.assertEqual(self.dashboard["features"]["bus_factor"], "AVAILABLE")
        self.assertEqual(
            self.dashboard["features"]["initial_excel_upload"],
            "AVAILABLE_PERSISTENT_CATALOG",
        )

    def test_star_map_exposes_general_and_quarter_modes(self) -> None:
        payload = self.client.get("/api/star-map").json()
        self.assertTrue(payload["teams"])
        for team in payload["teams"]:
            modes = team["competency_modes"]
            self.assertIn("all", modes)
            self.assertIn("quarter", modes)
            self.assertGreaterEqual(
                len(modes["all"]["technologies"]),
                len(modes["quarter"]["technologies"]),
            )
            self.assertTrue(
                all(item["team_bus_factor"] > 0 for item in modes["all"]["roles"])
            )
            for mode_name in ("all", "quarter"):
                counts = modes[mode_name]["zone_counts"]
                self.assertEqual(
                    counts["total"],
                    counts["critical"] + counts["limited"] + counts["stable"],
                )

    def test_star_map_frontend_has_quarter_toggle_and_static_graph(self) -> None:
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        html = (frontend / "index.html").read_text(encoding="utf-8")
        app_source = (frontend / "app.js").read_text(encoding="utf-8")
        styles = (frontend / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="starQuarterOnly"', html)
        self.assertIn("function starGraph", app_source)
        self.assertNotIn("startStarGraphForce", app_source)
        self.assertIn('id="selectedCompetencyPanel"', html)
        self.assertNotIn('id="page-star"', html)
        self.assertIn('data-risk-hub-tab="teams"', html)
        self.assertIn('data-risk-hub-tab="risks"', html)
        self.assertIn('data-risk-hub-tab="skills"', html)
        risk_tabs = html.split('aria-label="Разделы страницы рисков"', 1)[1].split('</div>', 1)[0]
        self.assertLess(risk_tabs.index('data-risk-hub-tab="risks"'), risk_tabs.index('data-risk-hub-tab="teams"'))
        self.assertLess(risk_tabs.index('data-risk-hub-tab="teams"'), risk_tabs.index('data-risk-hub-tab="skills"'))
        self.assertIn('class="subtab active" data-risk-hub-tab="risks"', risk_tabs)
        self.assertIn("riskHubTab: 'risks'", app_source)
        self.assertIn('risk-nav-has-risks', app_source)
        self.assertIn('risk-tab-has-risks', app_source)
        self.assertIn('updateRiskStatusIndicators();', app_source)
        self.assertIn('.nav-link.risk-nav-has-risks', styles)
        self.assertIn('.nav-link.risk-nav-clear', styles)
        self.assertNotIn('data-risk-hub-tab="distribution"', html)
        self.assertNotIn('id="starDistribution"', html)
        self.assertIn("data.starMap?.company", app_source)
        self.assertIn("step=\"0.01\" inputmode=\"decimal\"", (frontend / "constructor.js").read_text(encoding="utf-8"))
        self.assertIn("constructor-dropdown-menu.opens-up", styles)
        self.assertIn("item.scope === 'company' ? 'Вся компания'", app_source)
        self.assertIn('disabled aria-disabled="true"', app_source)
        self.assertIn("width:min(360px,calc(100% - 36px))", styles)
        self.assertIn("overflow-x:hidden;overflow-y:auto", styles)
        self.assertIn("grid-template-columns:repeat(auto-fit,minmax(125px,1fr))", styles)
        risk_page = html.split('id="page-risks"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('ПРОФИЛЬ КОМАНДЫ', risk_page)
        self.assertNotIn('КОМАНДА × КОМПЕТЕНЦИИ', risk_page)
        self.assertNotIn('data-archive-entity="team"', app_source)
        self.assertNotIn('id="teamMembers"', risk_page)
        self.assertNotIn('id="teamWorkload"', risk_page)
        self.assertNotIn('id="teamTasks"', risk_page)
        self.assertNotIn('ИНЖЕНЕРЫ × ТЕХНОЛОГИИ', risk_page)
        self.assertNotIn('Наличие, без выдуманных уровней', risk_page)
        self.assertNotIn('id="heatScope"', risk_page)

    def test_kpi_endpoint_exposes_structured_unavailable_fact_without_internal_ids(self) -> None:
        response = self.client.get("/api/kpis")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, self.dashboard["kpis"])
        self.assertNotIn("baseline_id", json.dumps(payload))
        self.assertNotIn("plan_version", json.dumps(payload))

        expected = payload["expected_kpi"]
        self.assertEqual(expected["completed_initiatives"], 2)
        with (PROCESSED_DIR / "tasks.csv").open(encoding="utf-8-sig", newline="") as source:
            initial_pool = {
                row["initiative_id"]
                for row in csv.DictReader(source)
                if row.get("initiative_id")
                and str(row.get("status") or "").strip().casefold()
                in {"todo", "inprogress"}
            }
        self.assertEqual(expected["baseline_initiatives"], len(initial_pool))
        self.assertEqual(
            expected["incomplete_initiatives"],
            len(initial_pool) - expected["completed_initiatives"],
        )
        self.assertAlmostEqual(
            expected["completion_percent"],
            expected["completed_initiatives"] / len(initial_pool) * 100,
        )

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
        index_source = (frontend / "index.html").read_text(encoding="utf-8")
        self.assertEqual(api_source.count("fetch("), 1)
        self.assertNotIn("fetch(", app_source)
        self.assertNotIn("fetch(", constructor_source)
        self.assertIn("escapeHtml", app_source)
        self.assertIn("localStorage", constructor_source)
        self.assertIn("buildConstructorPlan", constructor_source)
        self.assertIn("preflightConstructorDataset", constructor_source)
        self.assertIn("continueConstructorDataset", constructor_source)
        self.assertIn("previewPlan = planResult", constructor_source)
        self.assertIn("approveConstructorPlan", constructor_source)
        self.assertIn("invalidatePreview", constructor_source)
        self.assertIn("Помощь между командами", constructor_source)
        self.assertIn("Переносы и исключения", constructor_source)
        self.assertIn("hydrate(plannerResult)", app_source)
        self.assertIn("show('home')", app_source)
        self.assertIn("automaticEngineers", constructor_source)
        self.assertIn("catalogWithDraft('tasks')", constructor_source)
        self.assertIn("Number(right.expected_velocity_sp || 0)", constructor_source)
        self.assertIn('team_id: null', constructor_source)
        self.assertIn('list="constructorInitiativeOptions"', constructor_source)
        self.assertIn('name="dependency_id"', constructor_source)
        self.assertIn('name="team_engineer_id"', constructor_source)
        self.assertIn('Свободные инженеры', constructor_source)
        self.assertNotIn('Focus Factor', constructor_source)
        self.assertIn('+ Добавить задачу', index_source)
        self.assertIn("/api/plan-constructor/plan", api_source)
        self.assertIn("/api/admin/reset-data", api_source)
        self.assertIn('id="openResetData"', index_source)
        self.assertIn('id="resetDataDialog"', index_source)
        self.assertIn("postcode.plan-constructor.v2", app_source)
        self.assertIn("/api/plan-constructor/plan/upload", api_source)
        for old_demo_name in (
            "sampleTasks",
            "riskRecords",
            "initiativeNames",
            "demoChanges",
            "roleResources",
        ):
            self.assertNotIn(old_demo_name, app_source)


class ProductRiskSemanticsTests(unittest.TestCase):
    def test_dependency_chain_outside_quarter_becomes_red_alert(self) -> None:
        scenario = (
            SyntheticQuarter()
            .team("Team-A", velocity=100)
            .engineer("ENG-1", "Team-A", "Developer")
            .task(
                "BLOCKER",
                initiative_id="PRODF-RED",
                story_points=1,
                role_hours={"Developer": 400},
            )
            .task(
                "BLOCKED",
                initiative_id="PRODF-RED",
                story_points=1,
                role_hours={"Developer": 1},
            )
            .depends_on("BLOCKED", "BLOCKER")
        )
        service = PlanningAPIService.from_tables(
            {
                "tasks": scenario.tasks,
                "task_role_effort": scenario.effort,
                "task_dependencies": scenario.dependencies,
                "engineers": scenario.engineers,
                "engineer_skills": scenario.engineer_skills,
                "team_history": scenario.team_history,
            }
        )
        red = next(risk for risk in service.risks if risk["severity"] == "RED")
        self.assertEqual(red["code"], "DEADLINE_OUTSIDE_QUARTER")
        self.assertEqual(red["task_id"], "BLOCKED")
        self.assertEqual(red["initiative_id"], "PRODF-RED")
        self.assertGreater(red["sprint"], 6)
        self.assertIn("текущем квартале", red["effect"])


class FrontendRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frontend = PROCESSED_DIR.parents[1] / "frontend"
        cls.html = (cls.frontend / "index.html").read_text(encoding="utf-8")
        cls.app = (cls.frontend / "app.js").read_text(encoding="utf-8")
        cls.styles = (cls.frontend / "styles.css").read_text(encoding="utf-8")

    def test_top_navigation_includes_task_catalog(self) -> None:
        navigation = self.html.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn('data-page="constructor"', navigation)
        for page in ("home", "planning", "current-sprint", "tasks", "engineers", "teams", "risks"):
            self.assertIn(f'data-page="{page}"', navigation)
        self.assertNotIn('data-page="star"', navigation)
        self.assertIn('data-page="tasks">Задачи</button>', navigation)
        self.assertIn('data-page="engineers">Инженеры</button>', navigation)
        self.assertIn('data-page="teams">Команды</button>', navigation)
        self.assertIn('data-page="planning">Планирование</button>', navigation)
        self.assertNotIn('data-page="current-plan"', navigation)
        self.assertNotIn('>Текущий план</button>', navigation)
        self.assertIn('id="addTaskFromTasks"', self.html)
        self.assertIn("window.planConstructor.openTaskForm()", self.app)
        self.assertIn("postcode:constructor-task-added", self.app)

    def test_main_sections_use_supplied_page_icons(self) -> None:
        icon_names = (
            "current-plan", "planning", "current-sprint", "tasks",
            "engineers", "teams", "risks", "development",
        )
        for icon_name in icon_names:
            relative_path = f"/assets/page-icons/{icon_name}.png"
            self.assertIn(f'src="{relative_path}"', self.html)
            self.assertTrue((self.frontend / relative_path.removeprefix("/")).is_file())
        self.assertIn(".page-intro-icon.page-intro-image", self.styles)
        self.assertIn(".page-intro-image img", self.styles)

    def test_header_logo_is_constrained_to_brand_icon(self) -> None:
        self.assertIn('src="/assets/postcode-logo.jpg" width="47" height="47"', self.html)
        self.assertIn(".brand-icon{overflow:hidden;border:0;border-radius:0}", self.styles)
        self.assertIn(".brand-icon img{display:block;width:100%;height:100%;object-fit:cover}", self.styles)

    def test_engineer_and_team_catalogs_use_backend_collections(self) -> None:
        self.assertIn('id="page-engineers"', self.html)
        self.assertIn('id="engineersTableBody"', self.html)
        self.assertIn('id="page-teams"', self.html)
        self.assertIn('id="teamsTableBody"', self.html)
        self.assertIn("function renderEngineers()", self.app)
        self.assertIn("function renderTeams()", self.app)
        self.assertIn("data.engineers", self.app)
        self.assertIn("data.teams", self.app)
        self.assertIn('data-open-employee=', self.app)
        self.assertIn('data-open-team=', self.app)
        self.assertIn('id="addEngineerFromEngineers"', self.html)
        self.assertIn('id="addTeamFromTeams"', self.html)
        self.assertIn("window.planConstructor.openEngineerForm()", self.app)
        self.assertIn("window.planConstructor.openTeamForm()", self.app)

    def test_engineer_catalog_opens_stable_profile_page_and_explains_hidden_skills(self) -> None:
        self.assertIn("show('engineer-detail');", self.app)
        self.assertIn("loadEngineerDetail(engineerId);", self.app)
        self.assertIn("`/engineers/${encodeURIComponent(state.engineerId)}`", self.app)
        self.assertIn('class="catalog-skill-more"', self.app)
        self.assertIn('data-tooltip=', self.app)
        self.assertIn('data-engineer-sort="current_task"', self.html)
        self.assertNotIn('data-engineer-sort="free_hours"', self.html)
        self.assertIn("Нет активной задачи", self.app)

    def test_engineer_catalog_and_profile_omit_redundant_hints(self) -> None:
        for phrase in (
            "Нажмите на инженера, чтобы открыть его профиль.",
            "Capacity bucket каждого membership показан отдельно.",
            "Только плановые назначения из утверждённого плана.",
            "Квартальный расчёт всех подходящих направлений.",
        ):
            self.assertNotIn(phrase, self.html)
            self.assertNotIn(phrase, self.app)

    def test_team_roles_show_plain_role_badges(self) -> None:
        self.assertIn('class="team-role-badge"', self.app)
        self.assertNotIn("BF ${number(busFactor)}", self.app)
        self.assertIn(".team-role-badge", self.styles)

    def test_engineer_catalog_fits_without_horizontal_scrolling(self) -> None:
        self.assertIn("#engineersTableWrap{overflow-x:hidden}", self.styles)
        self.assertIn(".engineers-table{width:100%;min-width:0;table-layout:fixed", self.styles)

    def test_new_engineers_are_kept_in_scenario_draft(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("engineers: []", constructor_source)
        self.assertIn("data-form-type=\"engineers\"", constructor_source)
        self.assertIn("draft.engineers.forEach", constructor_source)
        self.assertIn("postcode:constructor-engineer-added", constructor_source)
        self.assertIn("openEngineerForm", constructor_source)
        self.assertIn("openTeamForm", constructor_source)

    def test_engineer_and_team_catalogs_sort_every_column(self) -> None:
        self.assertEqual(self.html.count('data-engineer-sort='), 8)
        self.assertEqual(self.html.count('data-team-sort='), 8)
        self.assertIn("state.engineerSortDirection", self.app)
        self.assertIn("state.teamSortDirection", self.app)
        self.assertIn("catalogSort(", self.app)

    def test_constructor_approves_preview_before_replacing_current_plan(self) -> None:
        self.assertIn("planning: window.planConstructor.render", self.app)
        self.assertIn("home: renderPlanning", self.app)
        self.assertIn("hydrate(plannerResult);", self.app)
        self.assertIn("show('home');", self.app)
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
        self.assertIn("Кто выполняет работу", constructor_source)
        self.assertIn("Основная команда", constructor_source)
        self.assertIn("Внешняя помощь", constructor_source)
        self.assertIn("Команда выполняет", constructor_source)
        self.assertNotIn("KPI: ${percent(effect.before_kpi)}", constructor_source)

    def test_constructor_preview_prioritizes_recommendations_and_groups_exclusions(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertLess(
            constructor_source.index('id="constructorSprintPlan"'),
            constructor_source.index('${recommendationPanel()}'),
        )
        self.assertIn('id="previewExclusionSort"', constructor_source)
        self.assertIn("function exclusionReasonMeta", constructor_source)
        self.assertIn("Не хватает специалистов", constructor_source)
        self.assertIn("Задача заблокирована зависимостью", constructor_source)
        self.assertIn("Не хватает доступного времени специалистов", constructor_source)
        self.assertIn("Недостаточно ресурсов команды", constructor_source)
        self.assertIn("Не помещается в текущем квартале", constructor_source)
        self.assertNotIn(">По причине</option>", constructor_source)
        self.assertIn("function filteredExcludedTasks", constructor_source)
        self.assertIn("Задач с причиной «${reason.label}» нет.", constructor_source)
        self.assertIn("preview-exclusion-empty", constructor_source)
        self.assertIn("MISSING_SPECIALIST", constructor_source)
        self.assertIn("DEPENDENCY_NOT_COMPLETED", constructor_source)
        self.assertIn("INSUFFICIENT_TEAM_CAPACITY", constructor_source)
        self.assertIn(".preview-help-list{max-height:280px", self.styles)
        self.assertIn(".preview-exclusion--critical", self.styles)
        self.assertIn(".preview-exclusion--capacity", self.styles)
        self.assertIn(".preview-exclusion--dependency", self.styles)
        self.assertIn(".recommendation-apply-button", self.styles)
        self.assertIn(".recommendation-reject-button", self.styles)
        self.assertNotIn("Отдельные изменения рассчитаны", constructor_source)
        self.assertNotIn("Сохранённый вариант не меняется", constructor_source)
        self.assertNotIn("Выберите причину, чтобы оставить", constructor_source)
        self.assertNotIn("Проверьте распределение перед утверждением", constructor_source)

    def test_header_navigation_uses_sliding_active_indicators(self) -> None:
        self.assertIn('class="nav-active-indicator"', self.html)
        self.assertIn('class="workspace-mode-indicator"', self.html)
        self.assertIn("function positionSlidingIndicator", self.app)
        self.assertIn("function updateHeaderIndicators", self.app)
        self.assertIn("requestAnimationFrame(updateHeaderIndicators)", self.app)
        self.assertIn(".nav.indicator-ready .nav-active-indicator", self.styles)
        self.assertIn(".workspace-mode-switch.indicator-ready .workspace-mode-indicator", self.styles)
        self.assertIn("cubic-bezier(.22,1,.36,1)", self.styles)

    def test_planning_opens_dataset_upload_before_manual_constructor(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("let mode = 'upload';", constructor_source)
        self.assertIn('class="constructor-mode-button active" type="button" role="tab" aria-selected="true" data-constructor-mode="upload"', self.html)
        self.assertIn('id="constructorManualMode" class="constructor-mode-view" hidden', self.html)
        self.assertIn('id="constructorUploadMode" class="constructor-mode-view"', self.html)
        self.assertLess(
            self.html.index('data-constructor-mode="upload"'),
            self.html.index('data-constructor-mode="manual"'),
        )
        self.assertIn('<span class="constructor-upload-icon" aria-hidden="true"><svg', self.html)
        self.assertNotIn('class="constructor-upload-icon" aria-hidden="true">↑', self.html)
        self.assertIn(".constructor-upload-icon svg", self.styles)

    def test_task_detail_groups_owner_team_and_external_help(self) -> None:
        self.assertIn("Основная команда —", self.app)
        self.assertIn("Помощь других команд", self.app)
        self.assertIn("task.ownership", self.app)
        self.assertIn("Команда выполняет ${number(ownership.owner_share_percent)}%", self.app)
        self.assertNotIn("'Cross-team'", self.app)
        self.assertNotIn("'Доля bucket'", self.app)

    def test_home_is_the_canonical_current_plan_view(self) -> None:
        navigation = self.html.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('data-page="home">Главная</button>', navigation)
        self.assertNotIn('data-page="current-plan"', navigation)
        self.assertNotIn('id="page-home"', self.html)
        self.assertIn('class="page active" id="page-current-plan"', self.html)
        self.assertIn("function canonicalPage(page)", self.app)
        self.assertIn("return page === 'current-plan' ? 'home' : page;", self.app)
        self.assertIn("home: renderPlanning", self.app)
        self.assertNotIn("function renderHome()", self.app)
        self.assertEqual(self.app.count("function renderPlanning()"), 1)

    def test_home_shows_live_plan_risk_counts(self) -> None:
        planning_html = self.html.split('id="page-current-plan"', 1)[1].split(
            'id="page-current-sprint"', 1
        )[0]
        render_risks = self.app.split("function renderPlanRiskSummary()", 1)[1].split(
            "function renderPlanning()", 1
        )[0]
        self.assertIn('id="planRiskSummary"', planning_html)
        self.assertIn("Внимание, риски по плану", render_risks)
        for severity in ("RED", "ORANGE", "YELLOW"):
            self.assertIn(f"counts.{severity}", render_risks)
        self.assertIn("target.classList.toggle('clear', clear)", render_risks)
        self.assertIn("${clear ? '✓' : '!'}", render_risks)
        self.assertIn('data-page="risks"', render_risks)
        self.assertIn(".plan-risk-summary{", self.styles)
        self.assertIn(".plan-risk-summary.clear{", self.styles)

    def test_home_navigation_keeps_legacy_and_contextual_returns(self) -> None:
        self.assertIn("'current-plan'", self.app.split("const knownPages", 1)[1].split(";", 1)[0])
        self.assertIn("page = canonicalPage(page);", self.app)
        self.assertIn("const viewPage = page === 'home' ? 'current-plan' : page;", self.app)
        self.assertIn("element.dataset.page === ({'task-detail': 'tasks'", self.app)
        self.assertIn('data-page="current-plan">← К текущему плану</button>', self.html)
        self.assertIn("const nav = event.target.closest('button[data-page]')", self.app)
        self.assertIn("taskReturnPage: 'home'", self.app)

    def test_technical_banner_and_header_version_are_absent(self) -> None:
        self.assertNotIn("dataModeRibbon", self.html + self.app)
        self.assertNotIn("header-quarter", self.html + self.app)
        self.assertNotIn("РЕАЛЬНЫЕ ДАННЫЕ", self.html + self.app)
        self.assertNotIn("Processed dataset", self.html + self.app)

    def test_old_home_landing_is_removed(self) -> None:
        self.assertNotIn("План на виду", self.html)
        self.assertNotIn("Команда в фокусе", self.html)
        self.assertNotIn('id="homeSprints"', self.html)
        self.assertNotIn('id="homeTasks"', self.html)
        self.assertNotIn('id="homeRisks"', self.html)
        self.assertNotIn("data-home-sprint", self.app)

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

    def test_current_sprint_uses_single_column_task_rows_with_details_action(self) -> None:
        current_sprint_page = self.html.split('id="page-current-sprint"', 1)[1].split(
            'id="page-tasks"', 1
        )[0]
        render_current_sprint = self.app.split("function renderCurrentSprint()", 1)[1].split(
            "function renderSprintCloseDialog", 1
        )[0]
        self.assertIn('id="currentSprintTasks"', current_sprint_page)
        self.assertEqual(current_sprint_page.count('data-current-sprint-sort='), 5)
        self.assertNotIn("Откройте задачу для подробностей", current_sprint_page)
        self.assertNotIn("Story Points и человеко-часы показаны раздельно", current_sprint_page)
        self.assertNotIn("Только реальные cross-team назначения текущего спринта", current_sprint_page)
        self.assertNotIn("Не в текущем спринте", current_sprint_page)
        self.assertNotIn("currentSprintLaterTasks", self.app)
        self.assertIn("current-sprint-task-more", render_current_sprint)
        self.assertIn("'Подробнее'", render_current_sprint)
        self.assertIn("rungBadge(task)", render_current_sprint)
        self.assertIn("data-toggle-current-sprint-task", render_current_sprint)
        self.assertIn("current-sprint-task-details", render_current_sprint)
        self.assertIn("Задействованные инженеры", render_current_sprint)
        self.assertIn("assignment.task_hours", render_current_sprint)
        self.assertIn("assignment.team_ids", render_current_sprint)
        self.assertIn("state.currentSprintSortKey", render_current_sprint)
        self.assertIn("state.currentSprintSortDirection", render_current_sprint)
        self.assertIn("data-current-sprint-sort", self.app)
        self.assertIn("current-sprint-task-title", render_current_sprint)
        self.assertIn(".current-sprint-task-list{grid-template-columns:1fr", self.styles)
        self.assertIn(".current-sprint-task-sort,.current-sprint-task-summary{display:grid", self.styles)
        self.assertIn("minmax(125px,.7fr) 155px", self.styles)
        self.assertIn("text-overflow:ellipsis;white-space:nowrap", self.styles)
        self.assertIn(".current-sprint-engineer-load", self.styles)

    def test_initiatives_and_excluded_tasks_use_backend_results(self) -> None:
        self.assertIn('id="completedInitiatives"', self.html)
        self.assertIn('id="incompleteInitiatives"', self.html)
        self.assertIn("initiative.completed_in_quarter", self.app)
        self.assertIn("task.reasons", self.app)
        self.assertIn("REASON_GROUPS", self.app)
        self.assertIn("Другая причина", self.app)

    def test_risk_list_and_detail_omit_redundant_hints(self) -> None:
        self.assertNotIn(
            "Нажмите на строку, чтобы открыть подробную карточку риска.",
            self.html,
        )
        self.assertNotIn("context.availability_note", self.app)
        self.assertNotIn("risk-bf-note", self.app)

    def test_task_catalog_and_detail_are_contextual(self) -> None:
        self.assertIn('id="page-tasks"', self.html)
        self.assertIn('id="page-task-detail"', self.html)
        self.assertIn('id="tasksTableBody"', self.html)
        self.assertIn('id="taskHeroId"', self.html)
        self.assertIn("$('#taskHeroId').textContent = task.task_id", self.app)
        self.assertIn("taskReturnPage", self.app)
        self.assertIn("taskReturnPage: 'home'", self.app)
        self.assertIn("show('task-detail')", self.app)

    def test_task_page_has_compact_copy_and_aligned_detail_actions(self) -> None:
        constructor = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        detail = self.app.split("function renderTaskDetail()", 1)[1].split(
            "function riskEvidenceValue", 1
        )[0]
        self.assertNotIn("Нажмите на ID или строку, чтобы открыть карточку задачи.", self.html)
        self.assertNotIn("Требуемые компетенции не указаны.", constructor)
        self.assertNotIn("Положение в квартальном плане", detail)
        self.assertNotIn(
            "Роли и трудозатраты из исходных требований задачи", detail
        )
        self.assertIn("#taskTopActions{display:flex", self.styles)
        self.assertIn("gap:12px;flex-wrap:wrap", self.styles)
        self.assertIn(".task-hero-meta{display:grid;justify-self:end;justify-items:stretch", self.styles)
        self.assertIn(".task-hero-status{display:flex;align-items:center;justify-content:center;width:100%", self.styles)

    def test_task_catalog_sorts_every_required_column(self) -> None:
        for key in (
            "task_id", "title", "story_points", "rung", "assigned_team_id",
            "initiative_id", "start_sprint", "status",
        ):
            self.assertIn(f'data-task-sort="{key}"', self.html)
        self.assertIn("state.taskSortDirection === 'asc'", self.app)
        self.assertIn("numericKeys", self.app)
        self.assertIn("aria-sort", self.app)
        self.assertIn("rungBadge(task)", self.app)
        self.assertIn("taskStatusIndicator(task)", self.app)
        self.assertIn("data-tooltip=", self.app)
        self.assertIn(".task-status-indicator", self.styles)

    def test_task_detail_uses_real_dependencies_and_cross_team_allocations(self) -> None:
        detail = self.app.split("function renderTaskDetail()", 1)[1].split(
            "function riskEvidenceValue", 1
        )[0]
        self.assertIn("task.dependencies?.incoming", detail)
        self.assertIn("task.dependencies?.outgoing", detail)
        self.assertIn('data-open-employee', detail)
        self.assertIn("item.cross_team", detail)
        self.assertIn("Другая команда", detail)
        self.assertIn("$('#taskTopActions').innerHTML", detail)
        self.assertIn("taskRoleRequirements(task)", detail)
        self.assertIn("renderTaskExecutionAlert(task)", detail)
        self.assertIn("task.reason_blocks", self.app)
        self.assertIn("Требуемые сотрудники", detail)
        self.assertIn("Сотрудники не назначены", detail)
        self.assertIn('id="taskTopActions"', self.html)
        self.assertIn('id="taskExecutionAlert"', self.html)
        self.assertEqual(detail.count('data-archive-entity="task"'), 1)
        self.assertNotIn("ОБЪЯСНЕНИЯ BACKEND", detail)
        self.assertNotIn("task.reasons", detail)

    def test_shareable_task_routes_serve_the_spa(self) -> None:
        client = TestClient(app)
        self.assertEqual(client.get("/tasks").status_code, 200)
        response = client.get("/tasks/AI-302")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="page-task-detail"', response.text)
        self.assertIn('href="/styles.css?v=', response.text)
        self.assertIn('src="/app.js?v=', response.text)

    def test_replanning_page_uses_live_review_flow(self) -> None:
        self.assertEqual(self.app.count("renderDevelopmentNotice('#"), 0)
        self.assertNotIn('id="risksDevelopmentNotice"', self.html)
        self.assertNotIn('id="recalcDevelopmentNotice"', self.html)
        self.assertIn('id="recalcResult"', self.html)
        self.assertIn("function renderRecalc", self.app)
        self.assertIn("hasPendingReplan", self.app)

    def test_risk_page_uses_three_actionable_alert_types(self) -> None:
        self.assertIn('data-risk-filter="Сроки"', self.html)
        self.assertIn('data-risk-filter="Ресурсы"', self.html)
        self.assertIn('data-risk-filter="Зависимости"', self.html)
        self.assertNotIn('data-risk-filter="Компетенции"', self.html)
        self.assertIn('>Уровень</th>', self.html)
        self.assertIn('renderRiskEvidence', self.app)
        self.assertIn('renderRiskBusFactor', self.app)
        self.assertIn('risk.severity_label', self.app)
        self.assertIn('risk-detail-meta', self.app)
        self.assertIn('risk-table-copy', self.app)
        self.assertIn('.risk-evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(84px,1fr))', self.styles)
        self.assertNotIn('.risk-detail{overflow-x:', self.styles)

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
        self.assertIn("KPI эффективности планирования", self.html)

    def test_kpi_details_use_one_collapsed_inline_toggle(self) -> None:
        self.assertIn('id="toggleKpiDetails"', self.html)
        self.assertIn('data-toggle-kpi-details', self.html)
        self.assertIn('aria-expanded="false"', self.html)
        self.assertIn('aria-controls="kpiDetails"', self.html)
        self.assertIn('id="kpiDetails" class="kpi-details" hidden', self.html)
        self.assertIn('Будут завершены в этом квартале', self.html)
        self.assertIn('Не будут завершены в этом квартале', self.html)
        self.assertIn('Почему задачи не вошли в план', self.html)
        self.assertNotIn('По текущему плану будут выполнены инициативы', self.html)
        self.assertIn('function updateKpiDetailsToggle()', self.app)
        self.assertIn('function toggleKpiDetails()', self.app)
        self.assertIn('state.kpiDetailsExpanded', self.app)
        self.assertIn('details.hidden = !state.kpiDetailsExpanded', self.app)
        self.assertIn('Подробнее <span aria-hidden="true">⌄</span>', self.app)
        self.assertIn('Скрыть подробности <span aria-hidden="true">⌃</span>', self.app)
        toggle_source = self.app.split('function toggleKpiDetails()', 1)[1].split('function renderPlanning()', 1)[0]
        self.assertNotIn('getDashboard', toggle_source)

    def test_frontend_only_formats_backend_kpi_values(self) -> None:
        render_kpis = self.app.split("function renderKpis()", 1)[1].split(
            "function renderPlanning()", 1
        )[0]
        self.assertIn("expected.completion_percent", render_kpis)
        self.assertIn("expected.completed_initiatives", render_kpis)
        self.assertIn("expected.baseline_initiatives", render_kpis)
        self.assertIn("quarter.completion_percent", render_kpis)
        self.assertIn("pi-kpi-comparison", render_kpis)
        self.assertNotIn("sprint_kpis", render_kpis)
        self.assertNotIn("Факт ещё не загружен", render_kpis)
        self.assertNotIn("Sprint Say/Do Ratio", render_kpis)
        planning_board = self.app.split("function renderPlanning()", 1)[1].split(
            "function renderTasks()", 1
        )[0]
        self.assertIn("data.kpis?.sprint_kpis", planning_board)
        self.assertIn("sprint-column-kpi", planning_board)
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
        self.assertIn("getDeletionImpact:", api)
        self.assertIn('id="archiveImpact"', self.html)
        self.assertIn('id="archiveReplanConfirm"', self.html)
        self.assertIn('name="teamEngineerAction"', self.app)
        self.assertIn('name="teamTaskAction"', self.app)
        self.assertIn('name="engineerAllocationAction"', self.app)
        self.assertIn("hydrate(result.dashboard)", self.app)

    def test_entity_cards_offer_archive_and_show_lifecycle(self) -> None:
        for entity_type in ("task", "engineer", "team"):
            self.assertIn(f'data-archive-entity="{entity_type}"', self.app)
        self.assertIn("archiveState(task)", self.app)
        self.assertIn("archiveState(engineer)", self.app)
        self.assertIn("archiveState(team)", self.app)
        self.assertIn("новый вариант будет рассчитан существующим planner", self.app.lower())

    def test_historical_done_dependency_is_rendered_as_satisfied(self) -> None:
        self.assertIn("item.state === 'HISTORICAL_DONE'", self.app)
        self.assertIn("dependency-satisfied", self.app)
        self.assertIn("item.status_message", self.app)

    def test_manual_constructor_keeps_historical_done_visible_and_read_only(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        self.assertIn("Завершена до начала планирования", constructor_source)
        self.assertIn("data-constructor-pick=\"tasks\"", constructor_source)
        self.assertIn("${completed ? 'disabled' : ''}", constructor_source)
        self.assertIn("data-open-task=\"${esc(task.task_id)}\"", constructor_source)

    def test_excel_preflight_modal_is_user_facing_and_requires_confirmation(self) -> None:
        constructor_source = (self.frontend / "constructor.js").read_text(encoding="utf-8")
        api_source = (self.frontend / "api.js").read_text(encoding="utf-8")
        styles = (self.frontend / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="dataPreflightDialog"', self.html)
        self.assertIn("Проверка данных", self.html)
        self.assertNotIn("ПЕРЕД ПОСТРОЕНИЕМ ПЛАНА", self.html)
        self.assertNotIn("Перед построением плана нашли", constructor_source)
        self.assertIn("Продолжить с этими данными", constructor_source)
        self.assertIn("Загрузить исправленный файл", constructor_source)
        self.assertIn("preflightConstructorDataset", api_source)
        self.assertIn("continueConstructorDataset", api_source)
        self.assertIn("preflight.status === 'blocked'", constructor_source)
        self.assertIn("preflight?.status === 'blocked' || preflight?.status === 'review_required'", constructor_source)
        self.assertIn("max-height:84vh", styles)


if __name__ == "__main__":
    unittest.main()
