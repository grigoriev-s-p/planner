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


def _extended_payload() -> dict:
    engineers = [
        {"engineer_id": "E1", "team_id": "A", "role": "Backend", "grade": "Senior", "capacity_rate": 1.0},
        {"engineer_id": "E2", "team_id": "A", "role": "Backend", "grade": "Middle", "capacity_rate": 0.5},
        {"engineer_id": "E2", "team_id": "B", "role": "Backend", "grade": "Middle", "capacity_rate": 0.5},
        {"engineer_id": "E3", "team_id": "B", "role": "QA", "grade": "Middle", "capacity_rate": 1.0},
    ]
    skills = [
        {"engineer_id": "E1", "technology": "Python"},
        {"engineer_id": "E1", "technology": "SQL"},
        {"engineer_id": "E2", "technology": "Python"},
        {"engineer_id": "E2", "technology": "Docker"},
        {"engineer_id": "E3", "technology": "Postman"},
    ]
    tasks = [{
        "task_id": "T1",
        "allocations": [
            {
                "engineer_id": "E1", "role": "Backend", "source_team_id": "A",
                "assigned_task_team_id": "B", "sprint": 1,
                "allocated_hours": 30, "cross_team": True,
            },
            {
                "engineer_id": "E2", "role": "Backend", "source_team_id": "B",
                "assigned_task_team_id": "B", "sprint": 1,
                "allocated_hours": 20, "cross_team": False,
            },
        ],
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
    quarter_python = next(item for item in company["competency_modes"]["quarter"]["technologies"] if item["technology"] == "Python")

    assert company["team_name"] == "Вся компания"
    assert company["member_count"] == 2
    assert python["team_bus_factor"] == 2
    assert python["organization_bus_factor"] == 2
    assert python["engineer_ids"] == ["E1", "E2"]
    assert quarter_python["team_bus_factor"] == 2
    assert quarter_python["organization_bus_factor"] == 2
    assert quarter_python["engineer_ids"] == ["E1", "E2"]


def test_star_map_reports_team_skill_coverage_percentages() -> None:
    team = next(item for item in _extended_payload()["teams"] if item["team_id"] == "A")
    coverage = {item["technology"]: item for item in team["skill_coverage"]}

    assert coverage["Python"]["coverage_pct"] == 100.0
    assert coverage["SQL"]["coverage_pct"] == 50.0


def test_star_map_role_capacity_uses_team_bucket_and_current_sprint_usage() -> None:
    team = next(item for item in _extended_payload()["teams"] if item["team_id"] == "A")
    backend = next(item for item in team["role_coverage"] if item["role"] == "Backend")

    assert backend["capacity_hours"] == 120.0
    assert backend["used_hours"] == 30.0
    assert backend["free_hours"] == 90.0


def test_star_map_multi_team_engineer_keeps_separate_capacity_buckets() -> None:
    engineer = next(
        item for item in _extended_payload()["engineers"] if item["engineer_id"] == "E2"
    )

    assert engineer["team_ids"] == ["A", "B"]
    assert engineer["capacity_hours"] == 80.0
    assert engineer["used_hours"] == 20.0
    assert engineer["free_hours"] == 60.0


def test_star_map_reserve_candidates_require_matching_role() -> None:
    engineer = next(
        item for item in _extended_payload()["engineers"] if item["engineer_id"] == "E1"
    )

    assert engineer["reserve_candidates"][0]["engineer_id"] == "E2"
    assert "E3" not in {item["engineer_id"] for item in engineer["reserve_candidates"]}


def test_star_map_quarter_skill_relevance_uses_local_allocated_engineers() -> None:
    team = next(item for item in _extended_payload()["teams"] if item["team_id"] == "B")
    relevance = {item["technology"]: item for item in team["quarter_skill_relevance"]}

    assert team["quarter_activity"]["engineer_ids"] == ["E2"]
    assert {"Python", "Docker"}.issubset(relevance)
    assert "Postman" not in relevance
    assert relevance["Python"]["allocated_hours"] == 20.0
