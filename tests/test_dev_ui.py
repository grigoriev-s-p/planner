from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dev_ui.data_access import derive_data_quality_issues, load_known_tables


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DevUiTests(unittest.TestCase):
    def test_real_processed_data_reconstructs_placeholder_warnings(self) -> None:
        tables, errors = load_known_tables()
        self.assertEqual(errors, [])
        issues = derive_data_quality_issues(tables)
        placeholders = issues[issues["issue_type"] == "MISSING_TASK"]
        self.assertEqual(len(placeholders), 7)
        self.assertEqual(set(placeholders["severity"]), {"WARNING"})

    def test_every_page_runs_against_real_processed_data(self) -> None:
        app = AppTest.from_file(PROJECT_ROOT / "dev_ui" / "app.py", default_timeout=20)
        app.run()
        self.assertEqual(list(app.exception), [])
        for page in (
            "Tasks",
            "Task Role Effort",
            "Dependencies",
            "Engineers / Skills",
            "Team History",
            "Data Quality",
            "Reference Data",
        ):
            app.sidebar.radio[0].set_value(page).run()
            self.assertEqual(list(app.exception), [], page)
            if page == "Dependencies":
                app.checkbox[1].set_value(True).run()
                self.assertEqual(list(app.exception), [], "Dependencies graph")


if __name__ == "__main__":
    unittest.main()
