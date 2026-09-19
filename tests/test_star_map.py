from __future__ import annotations

from src.star_map import StarMapService
from src.web.api import _company_star_map_team


def _payload() -> dict:
    engineers = [
        {"engineer_id": "E1", "team_id": "A", "role": "Backend", "capacity_rate": 1.0},
        {"engineer_id": "E2", "team_id": "A", "role": "Backend", "capacity_rate": 0.5},
        {"engineer_id": "E2", "team_id": "B", "role": "Backend", "capacity_rate": 0.5},
    ]
    skills = [
        {"engineer_id": "E1", "technology": "Python"},
        {"engineer_id": "E2", "technology": "Python"},
    ]
    tasks = [{
        "task_id": "T1",
        "allocations": [{
            "engineer_id": "E1", "role": "Backend", "source_team_id": "A",
            "assigned_task_team_id": "B", "sprint": 1,
            "allocated_hours": 20, "cross_team": True,
        }],
    }]
    return StarMapService(engineers, skills, tasks, current_sprint=1).payload()


def test_star_map_counts_unique_multi_team_engineer_once() -> None:
    team = next(item for item in _payload()["teams"] if item["team_id"] == "A")
    python = next(item for item in team["skill_coverage"] if item["technology"] == "Python")
    assert team["member_count"] == 2
    assert python["engineer_count"] == 2


def test_star_map_exposes_cross_team_flow() -> None:
    assert _payload()["cross_team_flows"] == [{
        "source_team_id": "A",
        "target_team_id": "B",
        "hours": 20.0,
        "engineer_ids": ["E1"],
        "task_ids": ["T1"],
    }]


def test_company_star_map_counts_unique_active_engineers() -> None:
    engineers = [
        {"engineer_id": "E1", "roles": ["Backend"], "skills": ["Python"]},
        {"engineer_id": "E2", "roles": ["Backend", "QA"], "skills": ["Python", "SQL"]},
    ]
    teams = [
        {"competency_modes": {"quarter": {"roles": [], "technologies": [
            {"technology": "Python", "engineer_ids": ["E1", "E2"]},
        ]}}},
        {"competency_modes": {"quarter": {"roles": [], "technologies": [
            {"technology": "Python", "engineer_ids": ["E2"]},
        ]}}},
    ]

    company = _company_star_map_team(engineers, teams)
    python = next(item for item in company["competency_modes"]["all"]["technologies"] if item["technology"] == "Python")

    assert company["team_name"] == "Вся компания"
    assert company["member_count"] == 2
    assert python["team_bus_factor"] == 2
    assert python["engineer_ids"] == ["E1", "E2"]
