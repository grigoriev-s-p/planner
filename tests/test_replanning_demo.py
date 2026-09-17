from __future__ import annotations

import unittest

from dev_ui.replanning_demo import DemoScenario, build_demo_snapshot
from src.replanning import (
    AlertCode,
    AlertLevel,
    ReferenceCatalog,
    validate_reference_usage,
)


CATALOG = ReferenceCatalog(
    (
        "1. Аналитика и архитектура выполнены частично",
        "не будет взято в квартал",
    ),
    ("Не оценивалась", "Отсутствие ресурсов"),
    (
        "планируемый результат работ достигнут",
        "планируемый результат работ НЕ достигнут",
    ),
)


class ReplanningDemoTests(unittest.TestCase):
    def test_shortage_scenario_shows_fact_forecast_and_required_alerts(self) -> None:
        snapshot = build_demo_snapshot(catalog=CATALOG)
        self.assertEqual(snapshot.pi_predictability_fact.percentage, 25)
        self.assertIsNotNone(snapshot.pi_predictability_forecast)
        self.assertEqual(snapshot.pi_predictability_forecast.percentage, 75)
        levels_by_code = {(alert.code, alert.level) for alert in snapshot.alerts}
        self.assertIn(
            (AlertCode.DEADLINE_OUTSIDE_QUARTER, AlertLevel.RED), levels_by_code
        )
        self.assertIn(
            (AlertCode.ROLE_CAPACITY_DEFICIT, AlertLevel.ORANGE), levels_by_code
        )
        self.assertIn((AlertCode.DEPENDENCY_SHIFT, AlertLevel.YELLOW), levels_by_code)

    def test_on_track_scenario_has_full_forecast_without_optional_bus_risk(self) -> None:
        snapshot = build_demo_snapshot(
            DemoScenario.ON_TRACK,
            critical_bus_factor=False,
            catalog=CATALOG,
        )
        self.assertEqual(snapshot.pi_predictability_forecast.percentage, 100)
        self.assertEqual(snapshot.alerts, ())

    def test_partial_sprint_fact_changes_fact_not_baseline(self) -> None:
        snapshot = build_demo_snapshot(
            delivered_story_points=3,
            catalog=CATALOG,
        )
        self.assertEqual(snapshot.pi_predictability_fact.percentage, 0)
        self.assertEqual(snapshot.sprint_say_do[0].numerator, 3)
        self.assertEqual(snapshot.sprint_say_do[0].denominator, 5)
        self.assertEqual(snapshot.sprint_say_do[0].percentage, 60)

    def test_demo_uses_only_source_reference_values(self) -> None:
        snapshot = build_demo_snapshot(catalog=CATALOG)
        self.assertEqual(
            validate_reference_usage(
                snapshot.current_plan,
                snapshot.actual_results,
                CATALOG,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
