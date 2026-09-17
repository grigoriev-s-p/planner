from __future__ import annotations

import inspect
import unittest

from dev_ui import upload_flow
from scripts.data_audit import (
    AuditSeverity,
    Issue,
    done_dependency_conflicts,
    machine_readable_audit,
    structural_audit_failure,
)


class UploadAuditFlowTests(unittest.TestCase):
    def test_valid_dataset_allows_planning(self) -> None:
        result = machine_readable_audit({"issues": []})
        self.assertTrue(result.planning_allowed)
        self.assertEqual(result.status, "ACCEPTED")

    def test_warning_only_allows_planning_and_is_visible(self) -> None:
        result = machine_readable_audit(
            {
                "issues": [
                    Issue(
                        "TASK_HOURS_TOTAL_MISMATCH",
                        "Estimated hours differ from role hours.",
                        task_ids=("T-1",),
                    )
                ]
            }
        )
        self.assertTrue(result.planning_allowed)
        self.assertEqual(result.issues[0].severity, AuditSeverity.WARNING)
        self.assertEqual(result.issues[0].affected_entity_ids, ("T-1",))

    def test_placeholder_blocks_only_that_entity(self) -> None:
        result = machine_readable_audit(
            {
                "issues": [
                    Issue(
                        "UNRESOLVED_PLACEHOLDER_TASK",
                        "Source task is missing.",
                        task_ids=("P-1",),
                    )
                ]
            }
        )
        self.assertTrue(result.planning_allowed)
        self.assertEqual(result.issues[0].severity, AuditSeverity.BLOCKING)
        self.assertFalse(result.issues[0].blocking)

    def test_structural_issue_blocks_planning(self) -> None:
        result = structural_audit_failure("tasks.csv is missing")
        self.assertFalse(result.planning_allowed)
        self.assertEqual(result.status, "CANNOT_PLAN")
        self.assertTrue(result.issues[0].blocking)

    def test_audit_result_is_machine_readable(self) -> None:
        payload = structural_audit_failure("bad structure").to_dict()
        issue = payload["issues"][0]
        self.assertEqual(
            set(issue),
            {
                "issue_code",
                "severity",
                "message",
                "affected_entity_type",
                "affected_entity_ids",
                "planning_impact",
                "blocking",
            },
        )

    def test_upload_flow_does_not_parse_markdown_for_business_logic(self) -> None:
        source = inspect.getsource(upload_flow)
        self.assertNotIn("render_report", source)
        self.assertNotIn(".md", source)

    def test_done_dependency_conflict_is_data_quality_warning(self) -> None:
        issues = done_dependency_conflicts(
            [
                {"task_id": "B", "status": "ToDo"},
                {"task_id": "A", "status": "Done"},
            ],
            [{"blocking_task_id": "B", "blocked_task_id": "A"}],
        )
        result = machine_readable_audit({"issues": issues})
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].issue_code, "DONE_DEPENDENCY_CONFLICT")
        self.assertEqual(result.issues[0].severity, AuditSeverity.WARNING)


if __name__ == "__main__":
    unittest.main()
