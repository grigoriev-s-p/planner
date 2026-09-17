from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from dev_ui.data_access import (
    count_unique_engineers,
    derive_data_quality_issues,
    load_known_tables,
    unique_engineer_counts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DevUiTests(unittest.TestCase):
    def test_engineer_totals_count_people_not_profile_rows(self) -> None:
        engineers = pd.DataFrame(
            [
                {"engineer_id": "E1", "team_id": "A", "role": "Backend"},
                {"engineer_id": "E1", "team_id": "A", "role": "QA"},
            ]
        )
        self.assertEqual(count_unique_engineers(engineers), 1)

    def test_engineer_aggregates_use_unique_ids_per_group(self) -> None:
        engineers = pd.DataFrame(
            [
                {"engineer_id": "E1", "team_id": "A", "role": "Backend"},
                {"engineer_id": "E1", "team_id": "A", "role": "Backend"},
                {"engineer_id": "E1", "team_id": "A", "role": "QA"},
            ]
        )
        by_role = unique_engineer_counts(engineers, "role", label="count")
        by_team = unique_engineer_counts(engineers, "team_id", label="count")
        self.assertEqual(by_role.to_dict(), {"Backend": 1, "QA": 1})
        self.assertEqual(by_team.to_dict(), {"A": 1})

    def test_real_processed_data_reconstructs_placeholder_warnings(self) -> None:
        tables, errors = load_known_tables()
        self.assertEqual(errors, [])
        issues = derive_data_quality_issues(tables)
        placeholders = issues[issues["issue_type"] == "MISSING_TASK"]
        task_rows = tables["tasks"]
        expected_ids = set(
            task_rows.loc[
                task_rows["is_placeholder"].astype(str).str.casefold() == "true",
                "task_id",
            ].astype(str)
        )
        self.assertEqual(set(placeholders["entity_id"].astype(str)), expected_ids)
        if expected_ids:
            self.assertEqual(set(placeholders["severity"]), {"WARNING"})

    def test_every_page_runs_against_real_processed_data(self) -> None:
        app = AppTest.from_file(PROJECT_ROOT / "dev_ui" / "app.py", default_timeout=20)
        app.run()
        self.assertEqual(list(app.exception), [])
        for page in (
            "KPI и пересчёт",
            "Задачи",
            "Трудозатраты по ролям",
            "Зависимости",
            "Инженеры и компетенции",
            "История производительности команд",
            "Качество данных",
            "Проверка выполнимости задачи",
            "Квартальный план",
            "Справочники",
        ):
            app.sidebar.radio[0].set_value(page).run()
            self.assertEqual(list(app.exception), [], page)
            if page == "Зависимости":
                app.checkbox[1].set_value(True).run()
                self.assertEqual(list(app.exception), [], "Граф зависимостей")
            if page == "Проверка выполнимости задачи":
                app.button[0].click().run()
                self.assertEqual(list(app.exception), [], "Результат проверки выполнимости")
            if page == "Квартальный план":
                app.button[0].click().run(timeout=30)
                self.assertEqual(list(app.exception), [], "Квартальный план")
                allocations = [
                    table.value for table in app.dataframe if "cross_team" in table.value.columns
                ]
                self.assertTrue(allocations, "Таблицы назначений инженеров")
                self.assertTrue(any(
                    (frame["cross_team"] == "Из другой команды").any()
                    for frame in allocations
                ))
                self.assertTrue(all(
                    {"teams", "task_team", "allocated_hours"}.issubset(frame.columns)
                    for frame in allocations
                ))
                completed_sp_tables = [
                    table.value
                    for table in app.dataframe
                    if {"completed_sp", "allocation_state"}.issubset(table.value.columns)
                ]
                self.assertTrue(completed_sp_tables, "Колонка завершённых SP")
                self.assertFalse(any(
                    "allocated_story_points" in frame.columns for frame in completed_sp_tables
                ))
                self.assertTrue(all(
                    {
                        "task_story_points",
                        "capacity_sp_load",
                        "planned_completion_sp",
                        "completed_sp",
                        "completion_sprint",
                    }.issubset(frame.columns)
                    for frame in completed_sp_tables
                ))

    def test_replanning_page_reacts_to_sprint_fact(self) -> None:
        app = AppTest.from_file(PROJECT_ROOT / "dev_ui" / "app.py", default_timeout=20)
        app.run()
        app.sidebar.radio[0].set_value("KPI и пересчёт").run()
        self.assertEqual(list(app.exception), [])
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["PI Predictability — FACT"], "25.0%")
        self.assertEqual(metrics["PI Predictability — FORECAST"], "75.0%")
        self.assertEqual(metrics["Sprint 1 Say/Do"], "100.0%")

        app.slider[0].set_value(3).run()
        self.assertEqual(list(app.exception), [])
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["PI Predictability — FACT"], "0.0%")
        self.assertEqual(metrics["Sprint 1 Say/Do"], "60.0%")


if __name__ == "__main__":
    unittest.main()
