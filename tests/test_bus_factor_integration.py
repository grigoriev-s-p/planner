from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.web.catalog import CatalogRepository
from src.web.service import build_bus_factor_payload


def catalog_tables(*, two_engineers: bool = False, with_skills: bool = True):
    engineers = [
        {
            "engineer_id": "E-1",
            "team_id": "Team-A",
            "role": "Backend",
            "grade": "Senior",
            "capacity_rate": 0.5,
        },
        {
            "engineer_id": "E-1",
            "team_id": "Team-B",
            "role": "Backend",
            "grade": "Senior",
            "capacity_rate": 0.5,
        },
    ]
    skills = [
        {"engineer_id": "E-1", "technology": "Kafka"},
        {"engineer_id": "E-1", "technology": "Kafka"},
    ] if with_skills else []
    if two_engineers:
        engineers.append(
            {
                "engineer_id": "E-2",
                "team_id": "Team-A",
                "role": "Backend",
                "grade": "Middle",
                "capacity_rate": 1.0,
            }
        )
        if with_skills:
            skills.append({"engineer_id": "E-2", "technology": "Kafka"})
    return {
        "tasks": [],
        "task_role_effort": [],
        "task_dependencies": [],
        "task_skill_requirements": [],
        "engineers": engineers,
        "engineer_skills": skills,
        "team_history": [
            {"team_id": "Team-A", "team_name": "Alpha"},
            {"team_id": "Team-B", "team_name": "Beta"},
        ],
    }


def competency(payload, team_id="Team-A", skill="Kafka"):
    team = next(item for item in payload["teams"] if item["team_id"] == team_id)
    return next(item for item in team["competencies"] if item["skill"] == skill)


class BusFactorProductIntegrationTests(unittest.TestCase):
    def test_one_engineer_with_two_memberships_is_counted_once(self) -> None:
        item = competency(build_bus_factor_payload(catalog_tables()))
        self.assertEqual(item["bus_factor"], 1)
        self.assertEqual(
            [engineer["engineer_id"] for engineer in item["engineers"]], ["E-1"]
        )
        self.assertEqual(item["risk_level"], "critical")

    def test_two_unique_engineers_produce_bus_factor_two(self) -> None:
        item = competency(
            build_bus_factor_payload(catalog_tables(two_engineers=True))
        )
        self.assertEqual(item["bus_factor"], 2)
        self.assertEqual(
            [engineer["engineer_id"] for engineer in item["engineers"]],
            ["E-1", "E-2"],
        )
        self.assertEqual(item["risk_level"], "warning")

    def test_archived_engineer_does_not_increase_current_bus_factor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CatalogRepository(
                Path(directory) / "catalog.sqlite3",
                seed_tables=catalog_tables(two_engineers=True),
            )
            repository.archive("engineer", "E-2", "EMPLOYEE_LEFT")
            payload = build_bus_factor_payload(repository.active_planning_tables())

        item = competency(payload)
        self.assertEqual(item["bus_factor"], 1)
        self.assertEqual(
            [engineer["engineer_id"] for engineer in item["engineers"]], ["E-1"]
        )
        self.assertNotIn("E-2", {item["engineer_id"] for item in payload["engineers"]})

    def test_team_without_skill_data_has_explicit_empty_state(self) -> None:
        payload = build_bus_factor_payload(catalog_tables(with_skills=False))
        team = next(item for item in payload["teams"] if item["team_id"] == "Team-A")
        self.assertFalse(team["bus_factor_available"])
        self.assertEqual(team["status"], "NO_COMPETENCY_DATA")
        self.assertEqual(team["competencies"], [])
        self.assertNotIn("bus_factor", team)


if __name__ == "__main__":
    unittest.main()
