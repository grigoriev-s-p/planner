from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.web.catalog import CatalogRepository
from src.web.constructor import ConstructorDatasetUploadError, PreparedConstructorUpload, _validated_archive_decisions


class ArchiveImportDecisionTests(unittest.TestCase):
    def repository(self) -> CatalogRepository:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repository = CatalogRepository(
            Path(directory.name) / "catalog.sqlite3",
            seed_tables={
                "tasks": [
                    {"task_id": "T-1", "summary": "Старая задача", "status": "ToDo"},
                    {"task_id": "T-2", "summary": "Вторая задача", "status": "ToDo"},
                ]
            },
        )
        repository.archive("task", "T-1", "OTHER", "Проверка архива")
        repository.archive("task", "T-2", "OTHER", "Проверка архива")
        return repository

    def test_keep_archived_preserves_record(self) -> None:
        repository = self.repository()
        result = repository.merge_tables(
            {"tasks": [{"task_id": "T-1", "summary": "Новое имя", "status": "ToDo"}]},
            archive_decisions={"task:T-1": "KEEP_ARCHIVED"},
        )
        self.assertEqual(result.kept_archived, {"task": 1})
        self.assertTrue(repository.lifecycle("task", "T-1")["archived"])
        self.assertEqual(
            next(row for row in repository.tables()["tasks"] if row["task_id"] == "T-1")["summary"],
            "Старая задача",
        )

    def test_restore_update_keeps_id_without_duplicate(self) -> None:
        repository = self.repository()
        result = repository.merge_tables(
            {"tasks": [{"task_id": "T-1", "summary": "Новое имя", "status": "ToDo"}]},
            archive_decisions={"task:T-1": "RESTORE_UPDATE"},
        )
        tasks = [row for row in repository.tables()["tasks"] if row["task_id"] == "T-1"]
        self.assertEqual(result.restored_updated, {"task": 1})
        self.assertFalse(repository.lifecycle("task", "T-1")["archived"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["summary"], "Новое имя")

    def test_different_decisions_are_applied_atomically(self) -> None:
        repository = self.repository()
        repository.merge_tables(
            {"tasks": [
                {"task_id": "T-1", "summary": "Новое имя", "status": "ToDo"},
                {"task_id": "T-2", "summary": "Новое второе имя", "status": "ToDo"},
            ]},
            archive_decisions={"task:T-1": "KEEP_ARCHIVED", "task:T-2": "RESTORE"},
        )
        self.assertTrue(repository.lifecycle("task", "T-1")["archived"])
        self.assertFalse(repository.lifecycle("task", "T-2")["archived"])
        self.assertEqual(
            next(row for row in repository.tables()["tasks"] if row["task_id"] == "T-1")["summary"],
            "Старая задача",
        )

    def test_repeating_same_confirmation_does_not_duplicate(self) -> None:
        repository = self.repository()
        incoming = {"tasks": [{"task_id": "T-1", "summary": "Новое имя", "status": "ToDo"}]}
        repository.merge_tables(incoming, archive_decisions={"task:T-1": "RESTORE_UPDATE"})
        repository.merge_tables(incoming, archive_decisions={"task:T-1": "RESTORE_UPDATE"})
        self.assertEqual(
            len([row for row in repository.tables()["tasks"] if row["task_id"] == "T-1"]),
            1,
        )

    def test_kept_archived_team_reference_is_rejected_before_merge(self) -> None:
        prepared = PreparedConstructorUpload(
            filename="test.xlsx", size_bytes=1, fingerprint="test",
            tables={"tasks": [{"task_id": "T-3", "team_id": "TEAM-OLD", "status": "ToDo"}]},
            validation_warnings=(),
            audit={"has_critical_errors": False, "archived_entities": [{"decision_key": "team:TEAM-OLD"}]},
        )
        with self.assertRaises(ConstructorDatasetUploadError):
            _validated_archive_decisions(prepared, {"team:TEAM-OLD": "KEEP_ARCHIVED"})


if __name__ == "__main__":
    unittest.main()
