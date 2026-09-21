from __future__ import annotations

import tempfile
from pathlib import Path

from src.data.normalization import NormalizedDataset
from src.web.catalog import CatalogRepository
from src.web.constructor import _deduplicate_normalized_dataset


def test_upload_deduplication_keeps_last_row_for_strict_identity() -> None:
    dataset = NormalizedDataset(
        tables={
            "tasks.csv": [
                {"task_id": "T-1", "summary": "Старое"},
                {"task_id": "T-1", "summary": "Исправленное"},
            ],
            "engineers.csv": [
                {"engineer_id": "E-1", "team_id": "A", "role": "Dev"},
                {"engineer_id": "E-1", "team_id": "A", "role": "Dev"},
                {"engineer_id": "E-1", "team_id": "B", "role": "QA"},
            ],
        },
        effort_task_ids=set(),
        effort_roles=set(),
        placeholder_sources={},
    )

    cleaned, summary = _deduplicate_normalized_dataset(dataset)

    assert cleaned.tables["tasks.csv"] == [{"task_id": "T-1", "summary": "Исправленное"}]
    assert len(cleaned.tables["engineers.csv"]) == 2
    assert summary["removed_total"] == 2


def test_existing_id_can_be_kept_or_updated_with_linked_rows() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = CatalogRepository(
            Path(directory) / "catalog.sqlite3",
            seed_tables={
                "tasks": [{"task_id": "T-1", "summary": "Текущее", "status": "ToDo"}],
                "task_role_effort": [{"task_id": "T-1", "role": "Dev", "required_hours": 8}],
                "task_skill_requirements": [
                    {"task_id": "T-1", "role": "Dev", "technology": "Legacy", "source": "explicit"}
                ],
            },
        )
        incoming = {
            "tasks": [{"task_id": "T-1", "summary": "Из Excel", "status": "ToDo"}],
            "task_role_effort": [{"task_id": "T-1", "role": "QA", "required_hours": 16}],
            "task_skill_requirements": [],
        }

        kept = repository.merge_tables(incoming, existing_decisions={"task:T-1": "KEEP_CURRENT"})
        assert repository.tables()["tasks"][0]["summary"] == "Текущее"
        assert kept.updated == {}

        updated = repository.merge_tables(incoming, existing_decisions={"task:T-1": "UPDATE"})
        tables = repository.tables()
        assert tables["tasks"] == [{"task_id": "T-1", "summary": "Из Excel", "status": "ToDo"}]
        assert tables["task_role_effort"] == [
            {"task_id": "T-1", "role": "QA", "required_hours": 16}
        ]
        assert tables["task_skill_requirements"] == []
        assert updated.updated == {"task": 1}
