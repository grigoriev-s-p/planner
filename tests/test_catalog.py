from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import src.web.catalog as catalog_module
import src.web.service as service_module
from src.web.api import app, plan_drafts
from src.web.catalog import (
    ArchiveConflictError,
    ArchiveValidationError,
    CatalogRepository,
)
from src.web.constructor import (
    ConstructorScenario,
    ScenarioValidationError,
    build_constructor_plan,
    validate_constructor_scenario,
)
from src.web.service import PlanningAPIService


def catalog_tables(*, blocker_status: str = "Done") -> dict[str, list[dict]]:
    return {
        "tasks": [
            {
                "task_id": "TASK-A",
                "initiative_id": "INIT-A",
                "summary": "Old title",
                "team_id": "Team-A",
                "status": blocker_status,
                "rung": 10,
                "story_points": 3,
                "is_placeholder": False,
            },
            {
                "task_id": "TASK-OLD",
                "initiative_id": "INIT-OLD",
                "summary": "Must remain",
                "team_id": "Team-A",
                "status": "ToDo",
                "rung": 1,
                "story_points": 1,
                "is_placeholder": False,
            },
        ],
        "task_role_effort": [
            {"task_id": "TASK-A", "role": "Backend", "required_hours": 8},
            {"task_id": "TASK-OLD", "role": "Backend", "required_hours": 8},
        ],
        "task_dependencies": [],
        "engineers": [
            {
                "engineer_id": "E-1",
                "name": "Engineer One",
                "team_id": "Team-A",
                "role": "Backend",
                "capacity_rate": 1,
            }
        ],
        "engineer_skills": [{"engineer_id": "E-1", "technology": "Python"}],
        "team_history": [
            {"team_id": "Team-A", "team_name": "Team A", "velocity_achieved": 10}
        ],
        "task_skill_requirements": [],
    }


def selected_b(*, blocker_id: str = "TASK-A") -> ConstructorScenario:
    return ConstructorScenario(
        teams=[
            {
                "team_id": "Team-A",
                "name": "Team A",
                "expected_velocity_sp": 10,
                "focus_factor": 0.8,
            }
        ],
        engineers=[
            {
                "engineer_id": "E-1",
                "name": "Engineer One",
                "assignments": [
                    {
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                "skills": ["Python"],
            }
        ],
        tasks=[
            {
                "task_id": "TASK-B",
                "title": "Blocked task",
                "initiative_id": "INIT-B",
                "team_id": "Team-A",
                "status": "ToDo",
                "rung": 8,
                "story_points": 3,
                "role_requirements": [
                    {"role": "Backend", "required_hours": 8, "skills": []}
                ],
                "depends_on": [blocker_id],
            }
        ],
    )


class CatalogMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        plan_drafts.reset()
        self.temp = tempfile.TemporaryDirectory()
        self.repo = CatalogRepository(
            Path(self.temp.name) / "catalog.sqlite3", seed_tables=catalog_tables()
        )

    def tearDown(self) -> None:
        plan_drafts.reset()
        self.temp.cleanup()

    def test_insert_only_merge_is_additive_idempotent_and_reports_conflicts(self) -> None:
        incoming = {
            "tasks": [
                {
                    "task_id": "TASK-A",
                    "summary": "New title",
                    "team_id": "Team-B",
                    "status": "ToDo",
                },
                {
                    "task_id": "TASK-B",
                    "summary": "New task",
                    "team_id": "Team-B",
                    "status": "ToDo",
                    "story_points": 2,
                },
            ],
            "task_role_effort": [
                {"task_id": "TASK-B", "role": "QA", "required_hours": 12}
            ],
            "task_dependencies": [
                {
                    "blocking_task_id": "TASK-A",
                    "blocked_task_id": "TASK-B",
                    "dependency_type": "depends on",
                }
            ],
            "engineers": [
                {
                    "engineer_id": "E-2",
                    "team_id": "Team-B",
                    "role": "QA",
                    "capacity_rate": 1,
                }
            ],
            "engineer_skills": [{"engineer_id": "E-2", "technology": "SQL"}],
            "team_history": [
                {"team_id": "Team-B", "team_name": "Team B", "velocity_achieved": 8}
            ],
            "task_skill_requirements": [],
        }

        first = self.repo.merge_tables(incoming)
        second = self.repo.merge_tables(incoming)
        tables = self.repo.tables()
        tasks = {row["task_id"]: row for row in tables["tasks"]}

        self.assertEqual(set(tasks), {"TASK-A", "TASK-B", "TASK-OLD"})
        self.assertEqual(tasks["TASK-A"]["summary"], "Old title")
        self.assertEqual(first.added["tasks"], 1)
        self.assertEqual(second.added["tasks"], 0)
        self.assertTrue(any("TASK-A уже существует" in item for item in first.warnings))
        self.assertEqual(
            len([row for row in tables["task_dependencies"] if row["blocked_task_id"] == "TASK-B"]),
            1,
        )
        self.assertTrue(any(row["engineer_id"] == "E-2" for row in tables["engineers"]))
        self.assertTrue(any(row["technology"] == "SQL" for row in tables["engineer_skills"]))
        self.assertTrue(any(row["team_id"] == "Team-B" for row in tables["team_history"]))

    def test_missing_from_import_does_not_remove_existing_entity(self) -> None:
        self.repo.merge_tables({"tasks": [catalog_tables()["tasks"][0]]})
        self.assertIn("TASK-OLD", {row["task_id"] for row in self.repo.tables()["tasks"]})

    def test_reset_all_data_clears_catalog_history_and_prevents_reseeding(self) -> None:
        self.repo.save_approved_plan({"plan": {"tasks": []}})
        self.repo.save_replan_workflow({"workflow_id": "workflow-1", "status": "DRAFT"})

        deleted = self.repo.reset_all_data()

        self.assertGreater(deleted["catalog_rows"], 0)
        self.assertEqual(deleted["plan_snapshots"], 1)
        self.assertEqual(deleted["replan_workflows"], 1)
        self.assertTrue(all(not rows for rows in self.repo.tables().values()))
        self.assertIsNone(self.repo.load_approved_plan())
        self.assertIsNone(self.repo.latest_replan_workflow())

        restarted = CatalogRepository(
            self.repo.database_path, seed_tables=catalog_tables()
        )
        self.assertTrue(all(not rows for rows in restarted.tables().values()))

    def test_reset_data_api_clears_persistent_and_transient_state(self) -> None:
        client = TestClient(app)
        draft = plan_drafts.create({"plan": {}, "tasks": []})
        with patch("src.web.api.get_catalog_repository", return_value=self.repo):
            response = client.post("/api/admin/reset-data")
            empty_dashboard = client.get("/api/dashboard")
            empty_catalog = client.get("/api/plan-constructor/catalog")
            empty_replan = client.get("/api/replanning/draft")
            health = client.get("/api/health")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "reset")
        self.assertTrue(all(not rows for rows in self.repo.tables().values()))
        with self.assertRaises(KeyError):
            plan_drafts.snapshot(draft["draft"]["id"])
        self.assertEqual(empty_dashboard.status_code, 200, empty_dashboard.text)
        self.assertEqual(empty_dashboard.json()["tasks"], [])
        self.assertEqual(empty_dashboard.json()["status"], "EMPTY")
        self.assertEqual(empty_dashboard.json()["data_state"], "UNINITIALIZED")
        self.assertEqual(empty_catalog.status_code, 200, empty_catalog.text)
        self.assertEqual(empty_catalog.json()["tasks"], [])
        self.assertEqual(empty_catalog.json()["status"], "EMPTY")
        self.assertEqual(empty_catalog.json()["data_state"], "UNINITIALIZED")
        self.assertEqual(empty_replan.status_code, 200, empty_replan.text)
        self.assertEqual(
            empty_replan.json(),
            {"status": "NONE", "data_state": "UNINITIALIZED"},
        )
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(
            health.json(),
            {"status": "ok", "data_state": "UNINITIALIZED"},
        )

    def test_repository_boots_empty_when_processed_snapshot_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            empty_processed = root / "processed"
            empty_processed.mkdir()
            database = root / "catalog.sqlite3"
            catalog_module.get_catalog_repository.cache_clear()
            try:
                with (
                    patch.object(catalog_module, "CATALOG_DB", database),
                    patch.object(service_module, "PROCESSED_DIR", empty_processed),
                    patch(
                        "src.web.service.get_planning_service",
                        side_effect=AssertionError("processed loader must not run"),
                    ),
                ):
                    repository = catalog_module.get_catalog_repository()
                    self.assertTrue(database.is_file())
                    self.assertTrue(all(not rows for rows in repository.tables().values()))
            finally:
                catalog_module.get_catalog_repository.cache_clear()

    def test_linked_data_conflict_is_reported_without_overwrite(self) -> None:
        result = self.repo.merge_tables({
            "task_role_effort": [
                {"task_id": "TASK-A", "role": "Backend", "required_hours": 24}
            ],
            "engineer_skills": [
                {"engineer_id": "E-1", "technology": "Java"}
            ],
        })

        self.assertEqual(
            [row for row in self.repo.tables()["task_role_effort"] if row["task_id"] == "TASK-A"],
            [{"task_id": "TASK-A", "role": "Backend", "required_hours": 8}],
        )
        self.assertEqual(
            [row for row in self.repo.tables()["engineer_skills"] if row["engineer_id"] == "E-1"],
            [{"engineer_id": "E-1", "technology": "Python"}],
        )
        self.assertEqual(
            {(item["table"], item["entity_id"]) for item in result.conflicts},
            {("task_role_effort", "TASK-A"), ("engineer_skills", "E-1")},
        )


class HistoricalDependencyTests(unittest.TestCase):
    def test_done_catalog_blocker_is_not_required_in_selection(self) -> None:
        result = build_constructor_plan(selected_b(), catalog_tables())
        by_id = {task["task_id"]: task for task in result["tasks"]}
        self.assertEqual(by_id["TASK-A"]["status"], "DONE")
        self.assertEqual(by_id["TASK-A"]["allocations"], [])
        task = by_id["TASK-B"]
        self.assertEqual(task["status"], "PLANNED")
        self.assertEqual(task["start_sprint"], 1)
        dependency = task["dependencies"]["incoming"][0]
        self.assertTrue(dependency["satisfied"])
        self.assertEqual(dependency["state"], "HISTORICAL_DONE")

    def test_active_unselected_blocker_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScenarioValidationError, "активная блокирующая"):
            validate_constructor_scenario(
                selected_b(), catalog_tables(blocker_status="ToDo")
            )

    def test_unknown_blocker_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScenarioValidationError, "неизвестна общему каталогу"):
            validate_constructor_scenario(
                selected_b(blocker_id="UNKNOWN-X"), catalog_tables()
            )


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        plan_drafts.reset()
        self.temp = tempfile.TemporaryDirectory()
        self.repo = CatalogRepository(
            Path(self.temp.name) / "catalog.sqlite3", seed_tables=catalog_tables()
        )

    def tearDown(self) -> None:
        plan_drafts.reset()
        self.temp.cleanup()

    def test_reason_is_required_and_other_requires_text(self) -> None:
        with self.assertRaises(ArchiveValidationError):
            self.repo.archive("engineer", "E-1", "")
        with self.assertRaises(ArchiveValidationError):
            self.repo.archive("engineer", "E-1", "OTHER", "   ")

    def test_archive_stores_reason_and_excludes_engineer_from_future_plan(self) -> None:
        historical_service = PlanningAPIService.from_tables(catalog_tables())
        result = self.repo.archive("engineer", "E-1", "EMPLOYEE_LEFT")

        self.assertTrue(result["archived"])
        self.assertEqual(result["archive_reason_code"], "EMPLOYEE_LEFT")
        self.assertFalse(self.repo.active_planning_tables()["engineers"])
        self.assertTrue(historical_service.tables["engineers"])

    def test_other_reason_text_is_preserved(self) -> None:
        result = self.repo.archive(
            "engineer", "E-1", "OTHER", "Переведён во внешний проект"
        )
        self.assertEqual(result["archive_reason_text"], "Переведён во внешний проект")

    def test_archived_entity_is_not_restored_by_import(self) -> None:
        self.repo.archive("engineer", "E-1", "EMPLOYEE_LEFT")
        result = self.repo.merge_tables(
            {"engineers": catalog_tables()["engineers"]}
        )
        self.assertTrue(self.repo.lifecycle("engineer", "E-1")["archived"])
        self.assertTrue(any("ранее была удалена" in item for item in result.warnings))

    def test_active_blocker_archive_is_blocked_but_done_blocker_is_preserved(self) -> None:
        active_tables = catalog_tables(blocker_status="ToDo")
        active_tables["task_dependencies"] = [
            {"blocking_task_id": "TASK-A", "blocked_task_id": "TASK-OLD", "dependency_type": "depends on"}
        ]
        active_repo = CatalogRepository(
            Path(self.temp.name) / "active.sqlite3", seed_tables=active_tables
        )
        with self.assertRaises(ArchiveConflictError):
            active_repo.archive("task", "TASK-A", "TASK_CANCELLED")

        self.repo.merge_tables(
            {"task_dependencies": active_tables["task_dependencies"]}
        )
        self.repo.archive("task", "TASK-A", "NO_LONGER_RELEVANT")
        planning = self.repo.active_planning_tables()
        self.assertIn("TASK-A", {row["task_id"] for row in planning["tasks"]})
        result = build_constructor_plan(selected_b(), self.repo.tables())
        by_id = {task["task_id"]: task for task in result["tasks"]}
        self.assertEqual(by_id["TASK-A"]["status"], "DONE")
        self.assertEqual(by_id["TASK-B"]["start_sprint"], 1)

    def test_team_archive_is_blocked_while_active_references_exist(self) -> None:
        with self.assertRaises(ArchiveConflictError):
            self.repo.archive("team", "Team-A", "TEAM_DISBANDED")

    def test_team_delete_can_keep_engineers_active_without_team_and_redistribute_tasks(self) -> None:
        result = self.repo.archive_with_relations(
            "team",
            "Team-A",
            "TEAM_DISBANDED",
            team_engineer_action="UNASSIGN",
            team_task_action="REDISTRIBUTE",
        )

        tables = self.repo.tables()
        engineer_rows = [row for row in tables["engineers"] if row["engineer_id"] == "E-1"]
        active = self.repo.active_planning_tables()
        task_old = next(row for row in tables["tasks"] if row["task_id"] == "TASK-OLD")
        self.assertTrue(result["entity"]["archived"])
        self.assertEqual(engineer_rows[0].get("team_id"), None)
        self.assertFalse(self.repo.lifecycle("engineer", "E-1")["archived"])
        self.assertIn("E-1", {row["engineer_id"] for row in active["engineers"]})
        self.assertIsNone(task_old.get("team_id"))
        self.assertNotIn("Team-A", {row["team_id"] for row in active["team_history"]})
        service = PlanningAPIService.from_tables(active)
        engineer = next(row for row in service.engineers if row["engineer_id"] == "E-1")
        self.assertEqual(engineer["team_ids"], [])
        self.assertIsNone(engineer["capacity_buckets"][0]["team_id"])

    def test_team_delete_can_deactivate_engineers_and_remove_tasks(self) -> None:
        result = self.repo.archive_with_relations(
            "team",
            "Team-A",
            "TEAM_DISBANDED",
            team_engineer_action="DEACTIVATE",
            team_task_action="REMOVE",
        )

        active = self.repo.active_planning_tables()
        self.assertEqual(result["affected_engineer_ids"], ["E-1"])
        self.assertIn("TASK-OLD", result["affected_task_ids"])
        self.assertTrue(self.repo.lifecycle("engineer", "E-1")["archived"])
        self.assertTrue(self.repo.lifecycle("task", "TASK-OLD")["archived"])
        self.assertNotIn("E-1", {row["engineer_id"] for row in active["engineers"]})
        self.assertNotIn("TASK-OLD", {row["task_id"] for row in active["tasks"]})

    def test_team_delete_is_atomic_when_removed_task_blocks_external_task(self) -> None:
        self.repo.merge_tables(
            {
                "tasks": [{
                    "task_id": "TASK-EXT", "initiative_id": "INIT-EXT",
                    "summary": "External", "team_id": "Team-B", "status": "ToDo",
                    "rung": 1, "story_points": 1,
                }],
                "task_role_effort": [],
                "task_dependencies": [{
                    "blocking_task_id": "TASK-OLD", "blocked_task_id": "TASK-EXT",
                    "dependency_type": "depends on",
                }],
                "engineers": [], "engineer_skills": [], "team_history": [],
                "task_skill_requirements": [],
            }
        )
        before = self.repo.tables()

        with self.assertRaises(ArchiveConflictError):
            self.repo.archive_with_relations(
                "team", "Team-A", "TEAM_DISBANDED",
                team_engineer_action="DEACTIVATE", team_task_action="REMOVE",
            )

        self.assertFalse(self.repo.lifecycle("team", "Team-A")["archived"])
        self.assertEqual(self.repo.tables(), before)

    def test_task_delete_rebuilds_plan_and_releases_allocations(self) -> None:
        client = TestClient(app)
        before = PlanningAPIService.from_tables(self.repo.active_planning_tables()).dashboard
        plan_drafts.replace_approved(before)
        with patch("src.web.api.get_catalog_repository", return_value=self.repo):
            response = client.post(
                "/api/tasks/TASK-OLD/archive",
                json={"reason_code": "TASK_CANCELLED", "confirm_replan": True},
            )

        self.assertEqual(response.status_code, 200, response.text)
        dashboard = response.json()["dashboard"]
        self.assertNotIn("TASK-OLD", {row["task_id"] for row in dashboard["tasks"]})
        self.assertFalse(any(
            allocation["task_id"] == "TASK-OLD"
            for allocation in dashboard["plan"]["allocations"]
        ))
        before_engineer = next(row for row in before["engineers"] if row["engineer_id"] == "E-1")
        after_engineer = next(row for row in dashboard["engineers"] if row["engineer_id"] == "E-1")
        self.assertGreater(
            sum(before_engineer["used_hours_by_sprint"].values()),
            sum(after_engineer["used_hours_by_sprint"].values()),
        )
        before_team = next(row for row in before["teams"] if row["team_id"] == "Team-A")
        after_team = next(row for row in dashboard["teams"] if row["team_id"] == "Team-A")
        self.assertGreater(
            sum(item["used"] for item in before_team["sp_by_sprint"].values()),
            sum(item["used"] for item in after_team["sp_by_sprint"].values()),
        )

    def test_engineer_delete_uses_existing_planner_for_replacement(self) -> None:
        tables = catalog_tables()
        tables["engineers"].append({
            "engineer_id": "E-2", "name": "Engineer Two", "team_id": "Team-A",
            "role": "Backend", "capacity_rate": 1,
        })
        tables["engineer_skills"].append({"engineer_id": "E-2", "technology": "Python"})
        repo = CatalogRepository(Path(self.temp.name) / "replacement.sqlite3", seed_tables=tables)
        client = TestClient(app)
        plan_drafts.replace_approved(
            PlanningAPIService.from_tables(repo.active_planning_tables()).dashboard
        )
        with patch("src.web.api.get_catalog_repository", return_value=repo):
            impact = client.get("/api/engineers/E-1/deletion-impact")
            response = client.post(
                "/api/engineers/E-1/archive",
                json={
                    "reason_code": "EMPLOYEE_LEFT", "confirm_replan": True,
                    "engineer_allocation_action": "APPLY_PLANNER",
                },
            )

        self.assertEqual(impact.status_code, 200, impact.text)
        self.assertTrue(impact.json()["replacement_options"])
        self.assertEqual(response.status_code, 200, response.text)
        dashboard = response.json()["dashboard"]
        self.assertNotIn("E-1", {row["engineer_id"] for row in dashboard["engineers"]})
        self.assertFalse(any(
            allocation["engineer_id"] == "E-1"
            for task in dashboard["tasks"]
            for allocation in task["allocations"]
        ))
        self.assertTrue(any(
            allocation["engineer_id"] == "E-2"
            for task in dashboard["tasks"]
            for allocation in task["allocations"]
        ))

    def test_engineer_delete_reports_when_no_replacement_exists(self) -> None:
        client = TestClient(app)
        plan_drafts.replace_approved(
            PlanningAPIService.from_tables(self.repo.active_planning_tables()).dashboard
        )
        with patch("src.web.api.get_catalog_repository", return_value=self.repo):
            impact = client.get("/api/engineers/E-1/deletion-impact")
            response = client.post(
                "/api/engineers/E-1/archive",
                json={
                    "reason_code": "EMPLOYEE_LEFT", "confirm_replan": True,
                    "engineer_allocation_action": "REPLAN_WITHOUT_REPLACEMENT",
                },
            )

        self.assertEqual(impact.status_code, 200, impact.text)
        self.assertTrue(any(
            not item["possible"] for item in impact.json()["replacement_options"]
        ))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["requires_plan_change"])
        self.assertIn("TASK-OLD", response.json()["unplannable_task_ids"])

    def test_team_redistribution_reports_tasks_that_cannot_be_replanned(self) -> None:
        tables = catalog_tables()
        tables["team_history"].append(
            {"team_id": "Team-B", "team_name": "Team B", "velocity_achieved": 10}
        )
        tables["engineers"].append({
            "engineer_id": "E-2", "team_id": "Team-B", "role": "QA",
            "capacity_rate": 1,
        })
        repo = CatalogRepository(Path(self.temp.name) / "unplannable.sqlite3", seed_tables=tables)
        client = TestClient(app)
        plan_drafts.replace_approved(
            PlanningAPIService.from_tables(repo.active_planning_tables()).dashboard
        )
        with patch("src.web.api.get_catalog_repository", return_value=repo):
            response = client.post(
                "/api/teams/Team-A/archive",
                json={
                    "reason_code": "TEAM_DISBANDED",
                    "confirm_replan": True,
                    "team_engineer_action": "UNASSIGN",
                    "team_task_action": "REDISTRIBUTE",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["requires_plan_change"])
        self.assertIn("TASK-OLD", payload["unplannable_task_ids"])
        engineer = next(row for row in payload["dashboard"]["engineers"] if row["engineer_id"] == "E-1")
        self.assertEqual(engineer["team_ids"], [])

    def test_archived_team_is_not_reactivated_by_later_import(self) -> None:
        tables = catalog_tables()
        tables["tasks"] = [tables["tasks"][0]]
        tables["task_role_effort"] = [tables["task_role_effort"][0]]
        tables["engineers"] = []
        tables["engineer_skills"] = []
        repo = CatalogRepository(
            Path(self.temp.name) / "archived-team.sqlite3", seed_tables=tables
        )
        repo.archive("team", "Team-A", "TEAM_DISBANDED")

        result = repo.merge_tables(
            {
                "tasks": [
                    {
                        "task_id": "TASK-NEW",
                        "initiative_id": "INIT-NEW",
                        "summary": "New task",
                        "team_id": "Team-A",
                        "status": "ToDo",
                        "story_points": 2,
                    }
                ],
                "task_role_effort": [
                    {"task_id": "TASK-NEW", "role": "Backend", "required_hours": 8}
                ],
                "engineers": [
                    {
                        "engineer_id": "E-NEW",
                        "team_id": "Team-A",
                        "role": "Backend",
                        "capacity_rate": 1,
                    }
                ],
                "engineer_skills": [],
                "team_history": tables["team_history"],
                "task_dependencies": [],
                "task_skill_requirements": [],
            }
        )

        active = repo.active_planning_tables()
        self.assertTrue(repo.lifecycle("team", "Team-A")["archived"])
        self.assertNotIn("TASK-NEW", {row["task_id"] for row in active["tasks"]})
        self.assertNotIn("E-NEW", {row["engineer_id"] for row in active["engineers"]})
        self.assertTrue(any("ранее была удалена" in item for item in result.warnings))

    def test_archive_api_rejects_missing_reason_and_accepts_structured_reason(self) -> None:
        client = TestClient(app)
        with patch("src.web.api.get_catalog_repository", return_value=self.repo):
            missing = client.post("/api/engineers/E-1/archive", json={})
            self.assertEqual(missing.status_code, 422)
            other = client.post(
                "/api/engineers/E-1/archive", json={"reason_code": "OTHER"}
            )
            self.assertEqual(other.status_code, 422)
            archived = client.post(
                "/api/engineers/E-1/archive",
                json={
                    "reason_code": "EMPLOYEE_LEFT",
                    "confirm_replan": True,
                    "engineer_allocation_action": "REPLAN_WITHOUT_REPLACEMENT",
                },
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertTrue(archived.json()["entity"]["archived"])


if __name__ == "__main__":
    unittest.main()
