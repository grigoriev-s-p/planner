from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from src.planning import FeasibilityContext, FeasibilityService, PlanStatus, SprintState, build_quarter_plan
from src.skills import (
    canonicalize_skill,
    extract_title_skills,
    get_skill_registry,
    merge_task_skill_requirements,
)
from src.web.api import app
from src.web.catalog import CatalogRepository


def _technologies(title: str) -> list[str]:
    return [item.display_name for item in extract_title_skills(title)]


def _planning_context(
    *,
    skills: list[dict],
    requirements: list[dict],
    engineers: list[dict] | None = None,
    effort: list[dict] | None = None,
) -> FeasibilityContext:
    return FeasibilityContext.from_records(
        tasks=[{
            "task_id": "T-1", "initiative_id": "I-1", "team_id": "Team-A",
            "summary": "Task", "status": "ToDo", "story_points": 1,
            "rung": 100, "is_placeholder": False,
        }],
        task_role_effort=effort or [
            {"task_id": "T-1", "role": "Backend", "required_hours": 8},
        ],
        dependencies=[],
        engineers=engineers or [
            {"engineer_id": "E-1", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
        ],
        engineer_skills=skills,
        team_history=[{"team_id": "Team-A", "velocity_achieved": 10}],
        task_skill_requirements=requirements,
    )


def test_registry_is_grounded_in_real_dataset_and_has_stable_metadata() -> None:
    registry = get_skill_registry()
    assert len(registry.definitions) >= 113
    postgresql = registry.definition("Postgres")
    assert postgresql is not None
    assert postgresql.display_name == "PostgreSQL"
    assert postgresql.canonical_id
    assert postgresql.group_id == "data"


def test_canonical_aliases_remain_distinct_where_required() -> None:
    assert canonicalize_skill("Postgres") == "PostgreSQL"
    assert canonicalize_skill("PostgresSQL") == "PostgreSQL"
    assert canonicalize_skill("CI CD") == "CI/CD"
    assert canonicalize_skill("CI-CD") == "CI/CD"
    assert canonicalize_skill("C#") == "C#"
    assert canonicalize_skill("C++") == "C++"
    assert canonicalize_skill("Java") != canonicalize_skill("JavaScript")
    assert canonicalize_skill("Microservices Архитектура") != canonicalize_skill("Microservices")


def test_title_extraction_respects_boundaries_symbols_and_multiple_matches() -> None:
    matches = extract_title_skills("Разработка JavaScript-компонента на React")
    assert [item.display_name for item in matches] == ["JavaScript", "React"]
    assert "Java" not in [item.display_name for item in matches]
    assert all(item.confidence == "high" for item in matches)
    assert [item.matched_text for item in matches] == ["JavaScript", "React"]
    assert _technologies("Поддержка C# и C++") == ["C#", "C++"]


def test_title_extraction_is_deterministic_and_does_not_guess() -> None:
    assert _technologies("") == []
    assert _technologies("Разработка серверной части приложения") == []
    assert _technologies("Развёртывание кластера и настройка CI CD") == ["CI/CD"]
    assert _technologies("Postgres и PostgreSQL") == ["PostgreSQL"]
    assert _technologies("Визуализация Grafana / Prometheus") == ["Grafana", "Prometheus"]


def test_explicit_and_title_requirements_merge_and_rejection_wins() -> None:
    merged = merge_task_skill_requirements(
        task_id="T-1",
        title="Миграция Postgres и интерфейс на JavaScript",
        explicit=[{"technology": "PostgreSQL", "role": "Backend"}],
        roles=["Backend", "Frontend"],
    )
    assert [(row["technology"], row["source"], row["role"]) for row in merged] == [
        ("JavaScript", "title", None),
        ("PostgreSQL", "both", "Backend"),
    ]
    rejected = merge_task_skill_requirements(
        task_id="T-1", title="React", roles=["Frontend"], rejected=["React"]
    )
    assert rejected == []


def test_role_level_skill_is_a_hard_constraint_but_empty_requirements_are_compatible() -> None:
    missing = build_quarter_plan(_planning_context(
        skills=[{"engineer_id": "E-1", "technology": "Java"}],
        requirements=[{"task_id": "T-1", "role": "Backend", "technology": "Postgres"}],
    ))
    result = missing.task_results[0]
    assert result.status != PlanStatus.PLANNED
    assert any(reason.code.value == "INSUFFICIENT_SKILL_CAPACITY" for reason in result.final_reasons)

    compatible = build_quarter_plan(_planning_context(
        skills=[], requirements=[],
    ))
    assert compatible.task_results[0].status == PlanStatus.PLANNED


def test_task_level_skills_are_covered_by_two_actually_allocated_engineers() -> None:
    engineers = [
        {"engineer_id": "E-B", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
        {"engineer_id": "E-F", "team_id": "Team-A", "role": "Frontend", "capacity_rate": 1},
    ]
    effort = [
        {"task_id": "T-1", "role": "Backend", "required_hours": 8},
        {"task_id": "T-1", "role": "Frontend", "required_hours": 8},
    ]
    plan = build_quarter_plan(_planning_context(
        engineers=engineers,
        effort=effort,
        skills=[
            {"engineer_id": "E-B", "technology": "Postgres"},
            {"engineer_id": "E-F", "technology": "React"},
        ],
        requirements=[
            {"task_id": "T-1", "role": None, "technology": "PostgreSQL"},
            {"task_id": "T-1", "role": None, "technology": "React"},
        ],
    ))
    result = plan.task_results[0]
    assert result.status == PlanStatus.PLANNED
    allocated = {
        row.engineer_id
        for allocation in result.allocations
        for row in allocation.engineer_allocations
        if row.hours > 0
    }
    assert allocated == {"E-B", "E-F"}


def test_unassigned_skill_holder_does_not_satisfy_task_level_coverage() -> None:
    engineers = [
        {"engineer_id": "E-B", "team_id": "Team-A", "role": "Backend", "capacity_rate": 1},
        {"engineer_id": "E-Q", "team_id": "Team-A", "role": "QA", "capacity_rate": 1},
    ]
    plan = build_quarter_plan(_planning_context(
        engineers=engineers,
        skills=[{"engineer_id": "E-Q", "technology": "React"}],
        requirements=[{"task_id": "T-1", "role": None, "technology": "React"}],
    ))
    result = plan.task_results[0]
    assert result.status != PlanStatus.PLANNED
    reasons = [reason for reason in result.final_reasons if reason.code.value == "INSUFFICIENT_SKILL_CAPACITY"]
    assert reasons
    assert "React" in reasons[0].details.get("missing_skills", [])


def test_skill_holder_without_available_hours_does_not_create_coverage() -> None:
    context = _planning_context(
        skills=[{"engineer_id": "E-1", "technology": "React"}],
        requirements=[{"task_id": "T-1", "role": None, "technology": "React"}],
    )
    result = FeasibilityService(context).check(
        "T-1",
        SprintState(
            sprint_number=1,
            engineer_hours_used={"E-1": 80},
            engineer_team_hours_used={"E-1": {"Team-A": 80}},
        ),
    )
    assert result.can_complete is False
    assert any(
        reason.code.value == "INSUFFICIENT_SKILL_CAPACITY"
        and "React" in reason.details.get("missing_skills", [])
        for reason in result.reasons
    )


def test_extraction_api_returns_structured_backend_preview() -> None:
    response = TestClient(app).post(
        "/api/skills/extract",
        json={"task_id": "T-1", "title": "Настройка CI/CD на Kubernetes", "roles": ["DevOps"]},
    )
    assert response.status_code == 200
    assert [row["technology"] for row in response.json()["requirements"]] == ["CI/CD", "Kubernetes"]
    assert all(row["role"] == "DevOps" for row in response.json()["requirements"])


def test_reimport_refreshes_only_automatic_requirements_and_keeps_overrides() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = CatalogRepository(
            Path(directory) / "catalog.sqlite3",
            seed_tables={
                "tasks": [{"task_id": "T-1", "summary": "Old"}],
                "task_role_effort": [], "task_dependencies": [], "engineers": [],
                "engineer_skills": [], "team_history": [],
                "task_skill_requirements": [
                    {"task_id": "T-1", "role": None, "technology": "React", "source": "explicit", "confirmed": True},
                    {"task_id": "T-1", "role": None, "technology": "Kafka", "source": "title", "rejected": True},
                    {"task_id": "T-1", "role": None, "technology": "Grafana", "source": "title"},
                ],
            },
        )
        repository.merge_tables({
            "tasks": [{"task_id": "T-1", "summary": "New"}],
            "task_skill_requirements": [
                {"task_id": "T-1", "role": None, "technology": "Kafka", "source": "title"},
                {"task_id": "T-1", "role": None, "technology": "Prometheus", "source": "title"},
            ],
        })
        rows = repository.tables()["task_skill_requirements"]
        by_skill = {row["technology"]: row for row in rows}
        assert set(by_skill) == {"React", "Kafka", "Prometheus"}
        assert by_skill["React"]["source"] == "explicit"
        assert by_skill["Kafka"]["rejected"] is True
