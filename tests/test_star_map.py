from __future__ import annotations

import unittest

from src.star_map import StarMapService


class StarMapServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engineers = [
            {"engineer_id": "E1", "team_id": "A", "role": "Backend", "grade": "Senior", "capacity_rate": 1.0},
            {"engineer_id": "E2", "team_id": "A", "role": "Backend", "grade": "Middle", "capacity_rate": 0.5},
            {"engineer_id": "E2", "team_id": "B", "role": "Backend", "grade": "Middle", "capacity_rate": 0.5},
            {"engineer_id": "E3", "team_id": "B", "role": "QA", "grade": "Middle", "capacity_rate": 1.0},
        ]
        self.skills = [
            {"engineer_id": "E1", "technology": "Python"},
            {"engineer_id": "E1", "technology": "SQL"},
            {"engineer_id": "E2", "technology": "Python"},
            {"engineer_id": "E2", "technology": "Docker"},
            {"engineer_id": "E3", "technology": "Postman"},
        ]
        self.tasks = [
            {
                "task_id": "T1",
                "allocations": [
                    {
                        "engineer_id": "E1",
                        "role": "Backend",
                        "source_team_id": "A",
                        "assigned_task_team_id": "B",
                        "sprint": 1,
                        "allocated_hours": 30,
                        "cross_team": True,
                    },
                    {
                        "engineer_id": "E2",
                        "role": "Backend",
                        "source_team_id": "B",
                        "assigned_task_team_id": "B",
                        "sprint": 1,
                        "allocated_hours": 20,
                        "cross_team": False,
                    },
                ],
            }
        ]
        self.payload = StarMapService(
            engineers=self.engineers,
            engineer_skills=self.skills,
            tasks=self.tasks,
            current_sprint=1,
        ).payload()

    def test_team_skill_coverage_uses_unique_team_members(self) -> None:
        team = next(item for item in self.payload["teams"] if item["team_id"] == "A")
        python = next(item for item in team["skill_coverage"] if item["technology"] == "Python")
        sql = next(item for item in team["skill_coverage"] if item["technology"] == "SQL")
        self.assertEqual(team["member_count"], 2)
        self.assertEqual(python["coverage_pct"], 100.0)
        self.assertEqual(sql["coverage_pct"], 50.0)

    def test_role_capacity_uses_team_bucket_and_current_sprint_usage(self) -> None:
        team = next(item for item in self.payload["teams"] if item["team_id"] == "A")
        backend = next(item for item in team["role_coverage"] if item["role"] == "Backend")
        self.assertEqual(backend["capacity_hours"], 120.0)
        self.assertEqual(backend["used_hours"], 30.0)
        self.assertEqual(backend["free_hours"], 90.0)

    def test_multi_team_engineer_keeps_separate_capacity_buckets(self) -> None:
        engineer = next(item for item in self.payload["engineers"] if item["engineer_id"] == "E2")
        self.assertEqual(engineer["team_ids"], ["A", "B"])
        self.assertEqual(engineer["capacity_hours"], 80.0)
        self.assertEqual(engineer["used_hours"], 20.0)
        self.assertEqual(engineer["free_hours"], 60.0)

    def test_cross_team_flow_is_exposed_once(self) -> None:
        self.assertEqual(
            self.payload["cross_team_flows"],
            [
                {
                    "source_team_id": "A",
                    "target_team_id": "B",
                    "hours": 30.0,
                    "engineer_ids": ["E1"],
                    "task_ids": ["T1"],
                }
            ],
        )

    def test_reserve_candidates_require_matching_role(self) -> None:
        engineer = next(item for item in self.payload["engineers"] if item["engineer_id"] == "E1")
        self.assertEqual(engineer["reserve_candidates"][0]["engineer_id"], "E2")
        self.assertNotIn("E3", {item["engineer_id"] for item in engineer["reserve_candidates"]})


    def test_quarter_skill_relevance_uses_local_engineers_allocated_to_team_tasks(self) -> None:
        team_b = next(item for item in self.payload["teams"] if item["team_id"] == "B")
        relevance = {item["technology"]: item for item in team_b["quarter_skill_relevance"]}
        self.assertEqual(team_b["quarter_activity"]["engineer_ids"], ["E2"])
        self.assertIn("Python", relevance)
        self.assertIn("Docker", relevance)
        self.assertNotIn("Postman", relevance)
        self.assertEqual(relevance["Python"]["allocated_hours"], 20.0)

    def test_bus_factor_contract_remains_unimplemented(self) -> None:
        self.assertIsNone(self.payload["bus_factor"])
        self.assertEqual(self.payload["bus_factor_status"], "NOT_IMPLEMENTED")


if __name__ == "__main__":
    unittest.main()
