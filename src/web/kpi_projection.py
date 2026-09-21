"""Management KPI projection over an approved plan and immutable Week-0 data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SAY_DO_MIN_PERCENT = 90.0
SAY_DO_MAX_PERCENT = 105.0
PI_MIN_PERCENT = 80.0
PI_MAX_PERCENT = 100.0


def _number(value: object) -> float:
    try:
        return max(float(value or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _status(percentage: float | None, lower: float, upper: float) -> str:
    if percentage is None:
        return "UNAVAILABLE"
    if percentage < lower:
        return "BELOW_TARGET"
    if percentage > upper:
        return "ABOVE_TARGET"
    return "ON_TARGET"


def _baseline_initiatives(state: dict[str, Any]) -> dict[str, set[str]]:
    tables = state.get("baseline_tables") or state.get("tables") or {}
    result: dict[str, set[str]] = {}
    for task in tables.get("tasks") or []:
        task_id = str(task.get("task_id") or "").strip()
        initiative_id = str(task.get("initiative_id") or "").strip()
        source_status = str(task.get("status") or "").strip().casefold()
        placeholder = str(task.get("is_placeholder") or "").strip().casefold()
        if (
            task_id
            and initiative_id
            and source_status in {"todo", "inprogress"}
            and placeholder not in {"true", "1", "yes"}
        ):
            result.setdefault(initiative_id, set()).add(task_id)
    return result


def _remaining_sp(
    task: dict[str, Any],
    outcome: dict[str, Any],
    original_tasks: dict[str, dict[str, Any]],
) -> float:
    task_id = str(task.get("task_id") or "")
    original = original_tasks.get(task_id) or {}
    original_sp = _number(original.get("original_story_points", task.get("story_points")))
    explicit_sp = outcome.get("remaining_story_points")
    if explicit_sp is not None:
        starting_sp = _number(
            outcome.get("starting_story_points")
            if outcome.get("starting_story_points") is not None
            else (
                task.get("remaining_story_points")
                if task.get("remaining_story_points") is not None
                else original_sp
            )
        )
        return min(_number(explicit_sp), starting_sp)
    original_hours = _number(original.get("original_total_required_hours"))
    remaining_hours = sum(
        _number(value) for value in (outcome.get("remaining_role_hours") or {}).values()
    )
    if original_hours > 0:
        return min(original_sp * remaining_hours / original_hours, original_sp)
    return min(_number(task.get("remaining_story_points")), original_sp)


def _sprint_fact(
    history: dict[int, dict[str, Any]],
    sprint_number: int,
    baseline_sp: float,
    original_tasks: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    """Return delivered and unplanned transferred SP for one closed sprint."""

    closed = history.get(sprint_number) or {}
    transferred_sp = 0.0
    for task in closed.get("tasks") or []:
        outcome = task.get("outcome") or {}
        if outcome.get("completed"):
            continue
        planned_task_sp = _number(task.get("sprint_capacity_sp_load"))
        # A task may have been intentionally split between several sprints.
        # Only the residual above its already planned future load is a miss of
        # the sprint being closed and must reduce Say/Do.
        planned_future_sp = sum(
            _number(value)
            for sprint, value in (task.get("sp_load_by_sprint") or {}).items()
            if int(sprint) > sprint_number
        )
        residual_sp = _remaining_sp(task, outcome, original_tasks)
        transferred_sp += min(
            max(residual_sp - planned_future_sp, 0.0), planned_task_sp
        )
    transferred_sp = min(transferred_sp, baseline_sp)
    return max(baseline_sp - transferred_sp, 0.0), transferred_sp


def _committed_sprint_sp(
    history: dict[int, dict[str, Any]],
    sprint_number: int,
    fallback: float,
) -> float:
    """Return the SP commitment frozen for this exact sprint.

    A future sprint may legitimately be empty in the Week-0 plan and receive
    work during replanning. ``capture_closed_sprint`` freezes the approved
    sprint immediately before it is closed, which is the correct Say/Do
    denominator. Week-0 is retained only as a compatibility fallback for old
    snapshots that do not contain closed-sprint history.
    """

    closed = history.get(sprint_number)
    if closed is None:
        return fallback
    return sum(
        _number(task.get("sprint_capacity_sp_load"))
        for task in closed.get("tasks") or []
    )


def project_management_kpis(payload: dict[str, Any]) -> dict[str, Any]:
    """Recalculate both management KPIs from immutable baseline and facts.

    PI uses the immutable Week-0 denominator. Sprint Say/Do uses the commitment
    frozen immediately before each sprint is closed. Facts come only from
    frozen closed-sprint history, so replanning cannot rewrite an observed result.
    """

    projected = deepcopy(payload)
    state = projected.get("_planning_state") or {}
    if not state:
        kpis = projected.get("kpis") or {}
        quarter = kpis.get("quarter_kpi") or {}
        quarter.update({
            "target_min_percent": PI_MIN_PERCENT,
            "target_max_percent": PI_MAX_PERCENT,
            "status": _status(
                quarter.get("completion_percent"), PI_MIN_PERCENT, PI_MAX_PERCENT
            ),
        })
        for row in kpis.get("sprint_kpis") or []:
            row.setdefault("remaining_sp", None)
            row.setdefault("transferred_sp", row.get("remaining_sp"))
            row.update({
                "target_min_percent": SAY_DO_MIN_PERCENT,
                "target_max_percent": SAY_DO_MAX_PERCENT,
                "status": _status(
                    row.get("completion_percent"),
                    SAY_DO_MIN_PERCENT,
                    SAY_DO_MAX_PERCENT,
                ),
            })
        return projected
    baseline = state.get("week0_baseline") or projected
    baseline_kpis = baseline.get("kpis") or {}
    completed_through = int(state.get("completed_through_sprint") or 0)
    completed_task_ids = {
        str(task_id)
        for task_id in (state.get("actual_completed_tasks") or {})
        if task_id
    }

    initiatives = _baseline_initiatives(state)
    completed_initiatives = {
        initiative_id
        for initiative_id, task_ids in initiatives.items()
        if task_ids and task_ids.issubset(completed_task_ids)
    }
    quarter_denominator = float(len(initiatives))
    quarter_numerator = float(len(completed_initiatives)) if completed_through else None
    quarter_percentage = (
        quarter_numerator / quarter_denominator * 100.0
        if quarter_numerator is not None and quarter_denominator > 0
        else None
    )

    history = {
        int(row.get("sprint_number") or 0): row
        for row in state.get("closed_sprint_history") or []
    }
    original_tasks = state.get("original_task_baseline") or {}
    baseline_rows = {
        int(row.get("sprint") or 0): row
        for row in baseline_kpis.get("sprint_kpis") or []
    }
    current_rows = {
        int(row.get("sprint") or 0): row
        for row in (projected.get("kpis") or {}).get("sprint_kpis") or []
    }
    sprint_kpis = []
    for sprint_number in range(1, 7):
        source = baseline_rows.get(sprint_number) or current_rows.get(sprint_number) or {}
        has_actual = sprint_number <= completed_through and sprint_number in history
        baseline_sp = _committed_sprint_sp(
            history,
            sprint_number,
            _number(source.get("baseline_sp")),
        ) if has_actual else _number(source.get("baseline_sp"))
        if has_actual:
            actual_sp, transferred_sp = _sprint_fact(
                history, sprint_number, baseline_sp, original_tasks
            )
            percentage = actual_sp / baseline_sp * 100.0 if baseline_sp > 0 else None
        else:
            actual_sp = None
            transferred_sp = None
            percentage = None
        sprint_kpis.append({
            "sprint": sprint_number,
            "baseline_sp": baseline_sp,
            "actual_sp": actual_sp,
            # ``remaining_sp`` is retained as a compatibility alias for old
            # API clients. New UI and integrations should use transferred_sp.
            "transferred_sp": transferred_sp,
            "remaining_sp": transferred_sp,
            "completion_percent": percentage,
            "has_actual": has_actual,
            "target_min_percent": SAY_DO_MIN_PERCENT,
            "target_max_percent": SAY_DO_MAX_PERCENT,
            "status": _status(percentage, SAY_DO_MIN_PERCENT, SAY_DO_MAX_PERCENT),
        })

    kpis = projected.setdefault("kpis", {})
    kpis["quarter_kpi"] = {
        "completed_initiatives": quarter_numerator,
        "baseline_initiatives": quarter_denominator,
        "completion_percent": quarter_percentage,
        "has_actual": completed_through > 0,
        "target_min_percent": PI_MIN_PERCENT,
        "target_max_percent": PI_MAX_PERCENT,
        "status": _status(quarter_percentage, PI_MIN_PERCENT, PI_MAX_PERCENT),
    }
    kpis["sprint_kpis"] = sprint_kpis
    return projected


def sprint_kpi(payload: dict[str, Any], sprint_number: int) -> dict[str, Any] | None:
    """Return one already projected Sprint Say/Do result."""

    return next(
        (
            deepcopy(row)
            for row in (payload.get("kpis") or {}).get("sprint_kpis") or []
            if int(row.get("sprint") or 0) == sprint_number
        ),
        None,
    )
