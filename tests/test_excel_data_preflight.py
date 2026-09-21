from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.data.normalization import NormalizedDataset
from src.data.parser import ParsedDataset, RawReferenceList, RawTable
from src.data.validation import ValidationReport
from src.web.api import app, prepared_uploads
from src.web.catalog import CatalogMergeResult, CatalogRepository
from src.web.constructor import PreparedConstructorUpload
from src.web.data_preflight import build_data_preflight


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def preflight_for(
    *,
    tasks: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    effort: list[dict] | None = None,
    engineers: list[dict] | None = None,
    skills: list[dict] | None = None,
    history: list[dict] | None = None,
    placeholder_sources: dict[str, tuple[str, ...]] | None = None,
    merge_warnings: tuple[str, ...] = (),
    merge_conflicts: tuple[dict, ...] = (),
) -> dict:
    tasks = tasks or [
        {
            "task_id": "B", "summary": "Active", "initiative_id": "I-1",
            "team_id": "Team-A", "status": "ToDo", "rung": 10,
            "story_points": 3, "is_placeholder": False,
        }
    ]
    dependencies = dependencies or []
    effort = effort or [{"task_id": "B", "role": "Backend", "required_hours": 8}]
    engineers = engineers or [
        {"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1.0}
    ]
    skills = skills if skills is not None else [{"engineer_id": "E-1", "technology": "Python"}]
    history = history if history is not None else [
        {"snapshot_date": "2026-01-01", "team_id": "Team-A", "velocity_achieved": 10, "planned_sp": 10}
    ]
    references = [{"source_order": 1, "value": "value"}]
    normalized = NormalizedDataset(
        tables={
            "tasks.csv": tasks,
            "task_role_effort.csv": effort,
            "task_dependencies.csv": dependencies,
            "engineers.csv": engineers,
            "engineer_skills.csv": skills,
            "team_history.csv": history,
            "planning_results.csv": references,
            "plan_mismatch_reasons.csv": references,
            "task_closure_results.csv": references,
        },
        effort_task_ids={row["task_id"] for row in effort},
        effort_roles={row["role"] for row in effort},
        placeholder_sources=placeholder_sources or {},
    )
    ref = RawReferenceList("ref", 50, "ref", ["value"])
    parsed = ParsedDataset(
        tasks=RawTable("tasks", 1, 2, [], [[None, row.get("task_id")] for row in tasks]),
        effort=RawTable("effort", 10, 11, [], [[row.get("role")] for row in effort]),
        dependencies=RawTable(
            "dependencies", 20, 21, [],
            [[row.get("blocking_task_id"), row.get("blocked_task_id"), row.get("dependency_type")] for row in dependencies],
        ),
        engineers=RawTable("engineers", 30, 31, [], [[row.get("engineer_id")] for row in engineers]),
        team_history=RawTable("team_history", 40, 41, [], [[row.get("snapshot_date"), row.get("team_id")] for row in history]),
        planning_results=ref,
        plan_mismatch_reasons=ref,
        task_closure_results=ref,
    )
    return build_data_preflight(
        parsed,
        normalized,
        ValidationReport(),
        CatalogMergeResult({}, {}, merge_warnings, merge_conflicts),
    )


def issue_codes(audit: dict) -> set[str]:
    return {
        item["code"]
        for group in audit["groups"]
        for item in group["items"]
    }


class DataPreflightAuditTests(unittest.TestCase):
    def test_active_catalog_conflict_exposes_strict_id_decision(self) -> None:
        conflict = {
            "table": "tasks", "entity_type": "task", "entity_id": "B",
            "saved": [{"task_id": "B", "summary": "Текущее"}],
            "excel": [{"task_id": "B", "summary": "Из Excel"}],
        }
        audit = preflight_for(
            merge_warnings=("Задача B уже существует. Оставлена текущая версия.",),
            merge_conflicts=(conflict,),
        )

        assert audit["existing_entities"][0]["decision_key"] == "task:B"
        assert audit["existing_entities"][0]["differences"] == [{
            "label": "Название", "current": "Текущее", "incoming": "Из Excel",
        }]

    def test_clean_dataset_has_no_dialog(self) -> None:
        audit = preflight_for()
        self.assertFalse(audit["show_dialog"])
        self.assertTrue(audit["can_continue"])

    def test_done_and_active_dependencies_are_not_data_problems(self) -> None:
        tasks = [
            {"task_id": "A", "team_id": "Team-A", "status": "Done", "rung": 1, "story_points": 2, "is_placeholder": False},
            {"task_id": "B", "team_id": "Team-A", "status": "ToDo", "rung": 2, "story_points": 3, "is_placeholder": False},
        ]
        for status in ("Done", "ToDo"):
            tasks[0]["status"] = status
            audit = preflight_for(
                tasks=[dict(row) for row in tasks],
                dependencies=[{"blocking_task_id": "A", "blocked_task_id": "B", "dependency_type": "depends on"}],
            )
            self.assertFalse(audit["has_critical_errors"])
            self.assertNotIn("UNKNOWN_DEPENDENCY_TASK", issue_codes(audit))

    def test_unknown_blocker_and_cycle_are_critical(self) -> None:
        placeholder = {"task_id": "MISSING", "status": None, "is_placeholder": True, "story_points": None}
        audit = preflight_for(
            tasks=[
                {"task_id": "B", "team_id": "Team-A", "status": "ToDo", "rung": 1, "story_points": 3, "is_placeholder": False},
                placeholder,
            ],
            dependencies=[{"blocking_task_id": "MISSING", "blocked_task_id": "B", "dependency_type": "depends on"}],
            placeholder_sources={"MISSING": ("Task_dependencies",)},
        )
        self.assertTrue(audit["has_critical_errors"])
        self.assertIn("UNKNOWN_DEPENDENCY_TASK", issue_codes(audit))

        cycle_tasks = [
            {"task_id": task_id, "team_id": "Team-A", "status": "ToDo", "rung": 1, "story_points": 1, "is_placeholder": False}
            for task_id in ("A", "B", "C")
        ]
        cycle = preflight_for(
            tasks=cycle_tasks,
            dependencies=[
                {"blocking_task_id": "A", "blocked_task_id": "B", "dependency_type": "depends on"},
                {"blocking_task_id": "B", "blocked_task_id": "C", "dependency_type": "depends on"},
                {"blocking_task_id": "C", "blocked_task_id": "A", "dependency_type": "depends on"},
            ],
        )
        self.assertIn("DEPENDENCY_CYCLE", issue_codes(cycle))

    def test_missing_roles_are_one_warning_with_all_roles(self) -> None:
        audit = preflight_for(effort=[
            {"task_id": "B", "role": role, "required_hours": 8}
            for role in ("QA", "Analyst", "DevOps")
        ])
        issues = [
            item for group in audit["groups"] for item in group["items"]
            if item["code"] == "MISSING_SPECIALISTS_FOR_ROLES"
        ]
        self.assertEqual(len(issues), 1)
        self.assertEqual(len(issues[0]["details"]), 3)
        self.assertEqual(issues[0]["severity"], "warning")

    def test_missing_skills_are_information_and_catalog_conflict_is_warning(self) -> None:
        audit = preflight_for(
            skills=[],
            merge_warnings=("Задача B уже существует. Данные из Excel не применены.",),
            merge_conflicts=({
                "entity_type": "task", "entity_id": "B", "table": "tasks",
                "saved": [{"task_id": "B", "rung": 8}],
                "excel": [{"task_id": "B", "rung": 10}],
            },),
        )
        self.assertIn("ENGINEER_SKILLS_MISSING", issue_codes(audit))
        self.assertIn("CATALOG_ENTITY_CONFLICT", issue_codes(audit))
        conflict = next(
            item for group in audit["groups"] for item in group["items"]
            if item["code"] == "CATALOG_ENTITY_CONFLICT"
        )
        self.assertEqual(
            conflict["details"],
            ["Приоритет Rung: в Excel — 10; сохранено — 8"],
        )
        self.assertFalse(audit["has_critical_errors"])

    def test_archived_entity_and_missing_history_are_non_blocking_warnings(self) -> None:
        audit = preflight_for(
            history=[],
            merge_warnings=(
                "Инженер E-1 удалена из активного каталога и не будет восстановлена из Excel.",
            ),
        )
        self.assertIn("ARCHIVED_ENTITY_IN_UPLOAD", issue_codes(audit))
        self.assertIn("TEAM_HISTORY_MISSING", issue_codes(audit))
        self.assertFalse(audit["has_critical_errors"])

    def test_self_and_duplicate_dependency_are_reported_once_with_source_row(self) -> None:
        link = {
            "blocking_task_id": "B", "blocked_task_id": "B",
            "dependency_type": "depends on",
        }
        audit = preflight_for(dependencies=[link, dict(link)])
        issues = [item for group in audit["groups"] for item in group["items"]]
        self_issue = next(item for item in issues if item["code"] == "SELF_DEPENDENCY")
        duplicate_issue = next(item for item in issues if item["code"] == "DUPLICATE_DEPENDENCY")
        self.assertEqual(self_issue["row"], 22)
        self.assertEqual(duplicate_issue["row"], 22)
        self.assertEqual(duplicate_issue["details"], ["Строки: 22, 23"])

    def test_invalid_task_resource_history_and_skill_references_are_classified(self) -> None:
        audit = preflight_for(
            tasks=[{
                "task_id": "B", "summary": "Active", "team_id": "Unknown-Team",
                "status": "ToDo", "rung": "high", "story_points": 3,
                "is_placeholder": False,
            }],
            engineers=[{
                "engineer_id": "E-1", "team_id": "Team-A", "role": "Backend",
                "capacity_rate": 1.5,
            }],
            skills=[{"engineer_id": "E-X", "technology": "Kafka"}],
            history=[{
                "snapshot_date": "2026-01-01", "team_id": "Team-A",
                "velocity_achieved": -1, "planned_sp": -2,
            }],
        )
        codes = issue_codes(audit)
        self.assertTrue({
            "INVALID_TASK_RUNG", "UNKNOWN_TASK_TEAM", "INVALID_CAPACITY_RATE",
            "INVALID_TEAM_VELOCITY", "INVALID_HISTORY_PLANNED_SP",
            "SKILL_UNKNOWN_ENGINEER",
        }.issubset(codes))
        self.assertTrue(audit["has_critical_errors"])

    def test_duplicate_history_is_a_warning(self) -> None:
        snapshot = {
            "snapshot_date": "2026-01-01", "team_id": "Team-A",
            "velocity_achieved": 10, "planned_sp": 10,
        }
        audit = preflight_for(history=[snapshot, dict(snapshot)])
        self.assertIn("DUPLICATE_TEAM_HISTORY", issue_codes(audit))
        self.assertFalse(audit["has_critical_errors"])

    def test_dry_run_uses_real_merge_rules_without_mutating_catalog(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            repository = CatalogRepository(Path(directory) / "catalog.sqlite3")
            incoming = {"tasks": [{"task_id": "T-1", "status": "ToDo"}]}
            preview = repository.merge_tables(incoming, dry_run=True)
            self.assertEqual(preview.added["tasks"], 1)
            self.assertEqual(repository.tables()["tasks"], [])


class DataPreflightAPIGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        prepared_uploads.reset()

    @staticmethod
    def prepared(audit: dict) -> PreparedConstructorUpload:
        return PreparedConstructorUpload(
            filename="test.xlsx", size_bytes=10, fingerprint="12345678",
            tables={}, validation_warnings=(), audit=audit,
        )

    def test_warning_requires_continue_and_planner_runs_once_after_acceptance(self) -> None:
        audit = preflight_for(skills=[])
        prepared = self.prepared(audit)
        with patch("src.web.api.prepare_constructor_workbook", return_value=prepared), \
             patch("src.web.api.build_constructor_plan_from_prepared", return_value={"plan": "ready"}) as build, \
             patch("src.web.api.plan_drafts.create", side_effect=lambda payload: payload):
            checked = self.client.post(
                "/api/plan-constructor/plan/upload", params={"filename": "test.xlsx"},
                content=b"123", headers={"Content-Type": "application/octet-stream"},
            )
            self.assertEqual(checked.json()["data_preflight"]["status"], "review_required")
            self.assertEqual(build.call_count, 0)
            upload_id = checked.json()["data_preflight"]["upload_id"]
            continued = self.client.post(f"/api/plan-constructor/plan/upload/{upload_id}/continue")
            self.assertEqual(continued.status_code, 200)
            self.assertEqual(build.call_count, 1)

    def test_critical_never_builds_and_has_no_continue_token(self) -> None:
        audit = preflight_for(
            tasks=[
                {"task_id": "B", "team_id": "Team-A", "status": "ToDo", "rung": 1, "story_points": 3, "is_placeholder": False},
                {"task_id": "MISSING", "status": None, "is_placeholder": True, "story_points": None},
            ],
            dependencies=[{"blocking_task_id": "MISSING", "blocked_task_id": "B", "dependency_type": "depends on"}],
            placeholder_sources={"MISSING": ("Task_dependencies",)},
        )
        with patch("src.web.api.prepare_constructor_workbook", return_value=self.prepared(audit)), \
             patch("src.web.api.build_constructor_plan_from_prepared") as build:
            response = self.client.post(
                "/api/plan-constructor/plan/upload", params={"filename": "test.xlsx"},
                content=b"123", headers={"Content-Type": "application/octet-stream"},
            )
        self.assertEqual(response.json()["data_preflight"]["status"], "blocked")
        self.assertIsNone(response.json()["data_preflight"]["upload_id"])
        build.assert_not_called()

    def test_clean_dataset_builds_immediately_without_review_dialog(self) -> None:
        audit = preflight_for()
        with patch("src.web.api.prepare_constructor_workbook", return_value=self.prepared(audit)), \
             patch("src.web.api.build_constructor_plan_from_prepared", return_value={"plan": "ready"}) as build, \
             patch("src.web.api.plan_drafts.create", side_effect=lambda payload: payload):
            response = self.client.post(
                "/api/plan-constructor/plan/upload", params={"filename": "test.xlsx"},
                content=b"123", headers={"Content-Type": "application/octet-stream"},
            )
        self.assertEqual(response.json()["data_preflight"]["status"], "clear")
        self.assertEqual(build.call_count, 1)


if __name__ == "__main__":
    unittest.main()
