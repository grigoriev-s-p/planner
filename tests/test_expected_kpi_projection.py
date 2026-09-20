from __future__ import annotations

from src.web.api import _apply_canonical_expected_initiatives


def _payload(
    baseline_tasks: list[dict],
    current_tasks: list[dict],
    actual_done: dict[str, int] | None = None,
) -> dict:
    initiatives = sorted({task["initiative_id"] for task in baseline_tasks})
    return {
        "tasks": current_tasks,
        "initiatives": [
            {"initiative_id": initiative, "completed_in_quarter": False, "task_ids": []}
            for initiative in initiatives
        ],
        "kpis": {
            "expected_kpi": {
                "completed_initiatives": 1.0,
                "baseline_initiatives": 15.0,
                "incomplete_initiatives": 14.0,
                "completion_percent": 100 / 15,
            }
        },
        "_planning_state": {
            "baseline_tables": {"tasks": baseline_tasks},
            "actual_completed_tasks": actual_done or {},
        },
        "plan": {"initiatives": []},
    }


def test_canonical_projection_matches_headline_and_lists_for_fifteen_initiatives() -> None:
    baseline = [
        {
            "task_id": f"TASK-{index}",
            "initiative_id": f"INIT-{index}",
            "status": "ToDo",
        }
        for index in range(1, 16)
    ]
    current = [
        {"task_id": "TASK-1", "initiative_id": "INIT-1", "status": "PLANNED", "completion_sprint": 4},
        {"task_id": "TASK-2", "initiative_id": "INIT-2", "status": "MOVED", "completion_sprint": 6},
    ]

    result = _apply_canonical_expected_initiatives(_payload(baseline, current))
    expected = result["kpis"]["expected_kpi"]
    completed = [row for row in result["initiatives"] if row["completed_in_quarter"]]
    incomplete = [row for row in result["initiatives"] if not row["completed_in_quarter"]]

    assert expected["completed_initiatives"] == len(completed) == 2.0
    assert expected["incomplete_initiatives"] == len(incomplete) == 13.0
    assert expected["baseline_initiatives"] == 15.0
    assert expected["completion_percent"] == 2 / 15 * 100


def test_moved_and_historical_done_tasks_still_complete_an_initiative() -> None:
    baseline = [
        {"task_id": "DONE", "initiative_id": "INIT-1", "status": "ToDo"},
        {"task_id": "MOVED", "initiative_id": "INIT-1", "status": "ToDo"},
    ]
    current = [
        {"task_id": "MOVED", "initiative_id": "INIT-1", "status": "MOVED", "completion_sprint": 4},
    ]

    result = _apply_canonical_expected_initiatives(
        _payload(baseline, current, {"DONE": 2})
    )

    initiative = result["initiatives"][0]
    assert initiative["completed_in_quarter"] is True
    assert initiative["task_ids"] == ["DONE", "MOVED"]


def test_removed_required_task_makes_initiative_incomplete_and_denominator_stays_immutable() -> None:
    baseline = [
        {"task_id": "DONE", "initiative_id": "INIT-1", "status": "ToDo"},
        {"task_id": "REMOVED", "initiative_id": "INIT-1", "status": "ToDo"},
    ]

    result = _apply_canonical_expected_initiatives(
        _payload(baseline, [{"task_id": "DONE", "initiative_id": "INIT-1", "status": "DONE"}], {"DONE": 1})
    )
    expected = result["kpis"]["expected_kpi"]

    assert result["initiatives"][0]["completed_in_quarter"] is False
    assert expected["completed_initiatives"] == 0.0
    assert expected["incomplete_initiatives"] == 1.0
    assert expected["baseline_initiatives"] == 1.0


def test_projection_does_not_replace_pre_approval_service_kpi_without_week0_state() -> None:
    payload = {
        "kpis": {"expected_kpi": {"completed_initiatives": 3.0}},
        "initiatives": [{"initiative_id": "INIT-1", "completed_in_quarter": True}],
    }

    result = _apply_canonical_expected_initiatives(payload)

    assert result == payload
