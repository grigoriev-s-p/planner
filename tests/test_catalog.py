from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.web.api import app
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
        self.temp = tempfile.TemporaryDirectory()
        self.repo = CatalogRepository(
            Path(self.temp.name) / "catalog.sqlite3", seed_tables=catalog_tables()
        )

    def tearDown(self) -> None:
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


class HistoricalDependencyTests(unittest.TestCase):
    def test_done_catalog_blocker_is_not_required_in_selection(self) -> None:
        result = build_constructor_plan(selected_b(), catalog_tables())
        self.assertEqual([task["task_id"] for task in result["tasks"]], ["TASK-B"])
        task = result["tasks"][0]
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
        self.temp = tempfile.TemporaryDirectory()
        self.repo = CatalogRepository(
            Path(self.temp.name) / "catalog.sqlite3", seed_tables=catalog_tables()
        )

    def tearDown(self) -> None:
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
        self.assertEqual(result["tasks"][0]["start_sprint"], 1)

    def test_team_archive_is_blocked_while_active_references_exist(self) -> None:
        with self.assertRaises(ArchiveConflictError):
            self.repo.archive("team", "Team-A", "TEAM_DISBANDED")

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
                json={"reason_code": "EMPLOYEE_LEFT"},
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertTrue(archived.json()["entity"]["archived"])


if __name__ == "__main__":
    unittest.main()
