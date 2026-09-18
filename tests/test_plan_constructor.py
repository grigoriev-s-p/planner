from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.web.api import PlanDraftStore, app, plan_drafts
from src.web.constructor import (
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_plan,
    build_constructor_catalog,
    scenario_tables,
    validate_constructor_scenario,
)
<<<<<<< Updated upstream
=======
from src.web.service import PlanningAPIService
from src.web.recommendations import RecommendationDraft
>>>>>>> Stashed changes


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

    def test_selected_done_task_stays_visible_but_uses_no_quarter_resources(self) -> None:
        payload = scenario_payload()
        done_task = {
            "task_id": "TASK-DONE", "title": "Historical task", "status": "Done",
            "rung": 0, "story_points": 0, "depends_on": [],
        }
        payload["tasks"].append(done_task)
        payload["tasks"][0]["depends_on"] = ["TASK-DONE"]
        result = build_constructor_plan(
            ConstructorScenario(**payload),
            catalog_tables={"tasks": [{"task_id": "TASK-DONE", "status": "ToDo"}]},
        )

        by_id = {task["task_id"]: task for task in result["tasks"]}
        done = by_id["TASK-DONE"]
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(done["status_label"], "Завершена до начала планирования")
        self.assertEqual(done["allocations"], [])
        self.assertEqual(done["planned_sprints"], [])
        self.assertEqual(done["sp_load_by_sprint"], {})
        self.assertEqual(by_id["TASK-1"]["start_sprint"], 1)
        self.assertEqual(by_id["TASK-1"]["dependencies"]["incoming"][0]["state"], "HISTORICAL_DONE")
        self.assertNotIn("TASK-DONE", {task["task_id"] for task in result["tasks"] if task["status"] in {"MOVED", "UNPLANNABLE"}})

    def test_global_done_status_overrides_scenario_active_status(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["status"] = "ToDo"
        result = build_constructor_plan(
            ConstructorScenario(**payload),
            catalog_tables={"tasks": [{"task_id": "TASK-1", "status": "Done"}]},
        )

        self.assertEqual(result["tasks"][0]["status"], "DONE")
        self.assertEqual(result["tasks"][0]["allocations"], [])

    def test_manual_catalog_keeps_done_visible_without_team_or_role_data(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0].update(status="Done", team_id=None, story_points=0, role_requirements=[])
        service = PlanningAPIService.from_tables(
            scenario_tables(ConstructorScenario(**payload)), plan_version="done-catalog-test"
        )

        catalog = build_constructor_catalog(service)

        historical = next(task for task in catalog["tasks"] if task["task_id"] == "TASK-1")
        self.assertEqual(historical["status"], "Done")
        self.assertEqual(historical["role_requirements"], [])
        self.assertIsNone(historical["team_id"])

    def test_unselected_historical_done_blocker_allows_dependent_in_sprint_one(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["depends_on"] = ["ARCHIVED-DONE"]
        catalog = {"tasks": [{"task_id": "ARCHIVED-DONE", "summary": "Archived blocker", "status": "Done", "archived": True}]}

        result = build_constructor_plan(ConstructorScenario(**payload), catalog_tables=catalog)

        task = next(item for item in result["tasks"] if item["task_id"] == "TASK-1")
        self.assertEqual(task["status"], "PLANNED")
        self.assertEqual(task["start_sprint"], 1)
        self.assertEqual(task["dependencies"]["incoming"][0]["state"], "HISTORICAL_DONE")
        historical = next(item for item in result["tasks"] if item["task_id"] == "ARCHIVED-DONE")
        self.assertEqual(historical["status"], "DONE")
        self.assertEqual(historical["allocations"], [])
        self.assertEqual(historical["planned_sprints"], [])

    def test_unselected_done_non_blocker_is_kept_as_historical_context(self) -> None:
        payload = scenario_payload()
        catalog = {
            "tasks": [
                {
                    "task_id": "DONE-NON-BLOCKER",
                    "summary": "Already completed",
                    "status": "Done",
                    "story_points": 3,
                }
            ]
        }

        tables = scenario_tables(ConstructorScenario(**payload), catalog)
        self.assertIn("DONE-NON-BLOCKER", {row["task_id"] for row in tables["tasks"]})

        result = build_constructor_plan(ConstructorScenario(**payload), catalog)
        historical = next(item for item in result["tasks"] if item["task_id"] == "DONE-NON-BLOCKER")
        self.assertEqual(historical["status"], "DONE")
        self.assertEqual(historical["planned_sprints"], [])
        self.assertEqual(historical["allocations"], [])
        self.assertEqual(historical["sp_load_by_sprint"], {})
        self.assertEqual(result["summary"]["done_tasks"], 1)

    def test_manual_all_active_matches_excel_base_with_all_done_context(self) -> None:
        payload = scenario_payload()
        catalog = {
            "tasks": [
                {
                    "task_id": "DONE-NON-BLOCKER",
                    "summary": "Already completed",
                    "status": "Done",
                    "initiative_id": "PRODF-HISTORY",
                    "team_id": "Team-A",
                    "rung": 1,
                    "story_points": 3,
                }
            ]
        }
        manual = build_constructor_plan(ConstructorScenario(**payload), catalog)

        excel_tables = scenario_tables(ConstructorScenario(**payload))
        excel_tables["tasks"].append(dict(catalog["tasks"][0]))
        excel = PlanningAPIService.from_tables(
            excel_tables,
            plan_version="excel-equivalent",
            planner_options={
                "allow_team_reassignment": False,
                "allow_cross_team": False,
            },
        ).dashboard

        def base_signature(result: dict) -> dict:
            plan = result["plan"]
            return {
                "summary": {
                    key: value
                    for key, value in result["summary"].items()
                    if key != "plan_version"
                },
                "tasks": sorted(
                    (
                        task["task_id"],
                        task["status"],
                        task["assigned_team_id"],
                        tuple(task["planned_sprints"]),
                        tuple(sorted(task["sp_load_by_sprint"].items())),
                        tuple(sorted(task["hours_by_sprint"].items())),
                        tuple(reason["code"] for reason in task["reasons"]),
                    )
                    for task in result["tasks"]
                ),
                "allocations": sorted(
                    (
                        row["task_id"], row["sprint"], row["engineer_id"],
                        row["role"], row["allocated_hours"], row["source_team_id"],
                    )
                    for row in plan["allocations"]
                ),
                "sprints": sorted(
                    (
                        sprint["sprint_number"],
                        tuple(task["task_id"] for task in sprint["tasks"]),
                        tuple(
                            sorted(
                                (team["team_id"], team["used_sp"])
                                for team in sprint["teams"]
                            )
                        ),
                    )
                    for sprint in plan["sprints"]
                ),
            }

        self.assertEqual(base_signature(manual), base_signature(excel))
        self.assertEqual(manual["summary"]["total_tasks"], 2)
        self.assertEqual(manual["summary"]["done_tasks"], 1)

    def test_unselected_active_and_unknown_blockers_have_distinct_errors(self) -> None:
        payload = scenario_payload()
        payload["tasks"][0]["depends_on"] = ["ACTIVE-BLOCKER"]
        scenario = ConstructorScenario(**payload)
        with self.assertRaisesRegex(ScenarioValidationError, "активная блокирующая задача ACTIVE-BLOCKER не включена"):
            validate_constructor_scenario(
                scenario, {"tasks": [{"task_id": "ACTIVE-BLOCKER", "status": "ToDo"}]}
            )

        payload["tasks"][0]["depends_on"] = ["UNKNOWN-BLOCKER"]
        with self.assertRaisesRegex(ScenarioValidationError, "UNKNOWN-BLOCKER неизвестна общему каталогу"):
            validate_constructor_scenario(ConstructorScenario(**payload), {"tasks": []})

    def test_inprogress_blocker_remains_active_planning_work(self) -> None:
        payload = scenario_payload()
        blocker = deepcopy(payload["tasks"][0])
        blocker.update(task_id="TASK-IN-PROGRESS", title="Started", status="InProgress", depends_on=[])
        payload["tasks"][0]["depends_on"] = ["TASK-IN-PROGRESS"]
        payload["tasks"].append(blocker)

        result = build_constructor_plan(ConstructorScenario(**payload))

        by_id = {task["task_id"]: task for task in result["tasks"]}
        self.assertEqual(by_id["TASK-IN-PROGRESS"]["status"], "PLANNED")
        self.assertGreater(by_id["TASK-1"]["start_sprint"], 1)
        self.assertNotEqual(by_id["TASK-1"]["dependencies"]["incoming"][0]["state"], "HISTORICAL_DONE")

    def test_approval_removes_historical_done_from_current_plan_dtos(self) -> None:
        payload = scenario_payload()
        catalog = {
            "tasks": [{
                "task_id": "TASK-DONE", "summary": "Historical task", "status": "Done",
                "rung": 0, "story_points": 5,
            }]
        }
        base = build_constructor_plan(ConstructorScenario(**payload), catalog)
        store = PlanDraftStore()
        draft = store.create(base)
        approved = store.approve(draft["draft"]["id"])

        self.assertIn("TASK-DONE", {task["task_id"] for task in base["tasks"]})
        self.assertNotIn("TASK-DONE", {task["task_id"] for task in approved["tasks"]})
        self.assertNotIn("TASK-DONE", {task["task_id"] for task in approved["plan"]["tasks"]})
        self.assertFalse(any(row.get("task_id") == "TASK-DONE" for row in approved["plan"]["allocations"]))
        self.assertFalse(any(
            row.get("task_id") == "TASK-DONE"
            for sprint in approved["plan"]["sprints"]
            for row in sprint["tasks"]
        ))
        self.assertTrue(all("TASK-DONE" not in item.get("task_ids", []) for item in approved["initiatives"]))
        self.assertTrue(all("TASK-DONE" not in item.get("task_ids", []) for item in approved["plan"]["initiatives"]))
        self.assertEqual(approved["summary"]["total_tasks"], 1)

    def test_optimizer_never_proposes_historical_done_task(self) -> None:
        payload = scenario_payload()
        payload["tasks"].append({
            "task_id": "TASK-DONE", "title": "Historical task", "status": "Done",
            "rung": 0, "story_points": 5, "depends_on": [],
            "role_requirements": [{"role": "Backend", "required_hours": 40, "skills": ["Python"]}],
        })
        base = build_constructor_plan(ConstructorScenario(**payload))
        draft = RecommendationDraft(base)
        fake_shadow = deepcopy(base)
        next(task for task in fake_shadow["tasks"] if task["task_id"] == "TASK-DONE")["assigned_team_id"] = "OTHER-TEAM"

        actions = draft._shadow_actions(fake_shadow)
        recommendations = list(draft.recommendations.values())

        self.assertFalse(any("TASK-DONE" in action.get("tasks", []) or action.get("task_id") == "TASK-DONE" for action in actions))
        self.assertFalse(any("TASK-DONE" in recommendation.get("tasks", []) for recommendation in recommendations))

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

<<<<<<< Updated upstream
=======
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
        approved_task = next(
            task for task in approval.json()["plan"]["tasks"]
            if task["task_id"] == "TASK-1"
        )
        draft_task = next(
            task for task in applied.json()["plan"]["tasks"]
            if task["task_id"] == "TASK-1"
        )
        self.assertEqual(approved_task, draft_task)
        self.assertFalse(any(task["status"] == "DONE" for task in approval.json()["tasks"]))
        self.assertTrue(any(
            row["cross_team"]
            for row in approval.json()["tasks"][0]["allocations"]
        ))

    def test_apply_all_changes_only_draft_until_approve(self) -> None:
        plan_drafts.reset()
        built = self.client.post("/api/plan-constructor/plan", json=ConstructorDomainTests.cross_team_payload())
        self.assertEqual(built.status_code, 200, built.text)
        draft = built.json()
        current_before = self.client.get("/api/dashboard").json()
        applied = self.client.post(f"/api/plan-constructor/drafts/{draft['draft']['id']}/recommendations/apply-all")
        self.assertEqual(applied.status_code, 200, applied.text)
        task = next(item for item in applied.json()["tasks"] if item["task_id"] == "TASK-1")
        self.assertEqual(task["status"], "PLANNED")
        self.assertTrue(any(item["status"] == "DONE" for item in applied.json()["tasks"]))
        self.assertEqual(self.client.get("/api/dashboard").json()["plan"], current_before["plan"])

>>>>>>> Stashed changes
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
        preflight = response.json()["data_preflight"]
        self.assertEqual(preflight["status"], "review_required")
        self.assertFalse(preflight["audit"]["has_critical_errors"])
        self.assertGreater(preflight["audit"]["summary"]["warning"], 0)

        continued = self.client.post(
            f"/api/plan-constructor/plan/upload/{preflight['upload_id']}/continue"
        )
        self.assertEqual(continued.status_code, 200, continued.text)
        payload = continued.json()
        self.assertEqual(payload["uploaded_dataset"]["name"], RAW_WORKBOOK.name)
        self.assertTrue(payload["uploaded_dataset"]["server_persisted"])
        self.assertIn("catalog_merge", payload["uploaded_dataset"])
        self.assertEqual(len(payload["plan"]["sprints"]), 6)
        self.assertTrue(payload["teams"])
        self.assertTrue(payload["engineers"])
        self.assertTrue(payload["tasks"])
        historical_done = [task for task in payload["tasks"] if task["status"] == "DONE"]
        self.assertTrue(historical_done)
        self.assertTrue(all(task["status_label"] == "Завершена до начала планирования" for task in historical_done))
        self.assertTrue(all(not task["planned_sprints"] and not task["allocations"] for task in historical_done))
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

        self.assertEqual(response.status_code, 200)
        preflight = response.json()["data_preflight"]
        self.assertEqual(preflight["status"], "blocked")
        self.assertTrue(preflight["audit"]["has_critical_errors"])
        self.assertIsNone(preflight["upload_id"])

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
