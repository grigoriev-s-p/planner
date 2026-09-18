"""Explainable business scoring for quarterly planning candidates."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


SCORING_VERSION = "v1"
PLANNABLE_STATUSES = frozenset({"todo", "inprogress"})
COMPLETED_STATUS = "done"


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or (
        isinstance(value, str) and not value.strip()
    )


def _text(value: Any) -> str | None:
    return None if _missing(value) else str(value).strip()


def _number(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _boolean(value: Any) -> bool:
    return value is True or str(value).strip().casefold() in {"true", "1", "yes"}


def date_ordinal(value: Any) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text).date().toordinal()
    except ValueError:
        try:
            return date.fromisoformat(text[:10]).toordinal()
        except ValueError:
            return None


@dataclass(frozen=True)
class ScoringConfig:
    """Scoring v1 weights; the default rung direction is organizer-confirmed."""

    rung_weight: float = 40.0
    in_progress_bonus: float = 25.0
    initiative_completion_weight: float = 20.0
    dependency_unlock_weight: float = 10.0
    # Retained as a zero-valued compatibility field. Confirmed business rules
    # treat planned_end as metadata, never as a score or ordering signal.
    deadline_weight: float = 0.0
    # Confirmed business rule: higher rung = higher priority. The field remains
    # in the public config, but the superseded lower-is-better mode is rejected.
    rung_direction: Literal["higher_is_better"] = "higher_is_better"
    dependency_unlock_saturation_count: int = 4

    def __post_init__(self) -> None:
        weights = (
            self.rung_weight,
            self.in_progress_bonus,
            self.initiative_completion_weight,
            self.dependency_unlock_weight,
            self.deadline_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("Scoring weights cannot be negative.")
        if self.rung_direction != "higher_is_better":
            raise ValueError("rung_direction must be 'higher_is_better'.")
        if self.dependency_unlock_saturation_count <= 0:
            raise ValueError("dependency_unlock_saturation_count must be positive.")


@dataclass(frozen=True)
class TaskScore:
    task_id: str
    total_score: float
    base_score: float
    dynamic_score: float
    components: dict[str, float]
    scoring_version: str = SCORING_VERSION
    eligible: bool = True
    not_eligible_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "total_score": self.total_score,
            "base_score": self.base_score,
            "dynamic_score": self.dynamic_score,
            "components": dict(self.components),
            "scoring_version": self.scoring_version,
            "eligible": self.eligible,
            "not_eligible_reason": self.not_eligible_reason,
        }


@dataclass(frozen=True)
class PlanningContext:
    """Business-only state used by scoring; it deliberately contains no capacity."""

    done_task_ids: frozenset[str]
    committed_task_ids: frozenset[str]
    remaining_task_ids: frozenset[str]
    eligible_task_ids: frozenset[str]
    excluded_task_ids: frozenset[str]
    placeholder_task_ids: frozenset[str]
    initiative_membership: dict[str, frozenset[str]]
    reliable_initiative_ids: frozenset[str]
    dependency_graph: dict[str, frozenset[str]]
    rung_values: dict[str, float]
    planned_end_ordinals: dict[str, int]
    source_tasks: dict[str, dict[str, Any]] = field(repr=False)

    @classmethod
    def from_records(
        cls,
        tasks: Iterable[Mapping[str, Any]],
        dependencies: Iterable[Mapping[str, Any]],
        *,
        committed_task_ids: Iterable[str] = (),
        excluded_task_ids: Iterable[str] = (),
    ) -> "PlanningContext":
        source_tasks = {
            task_id: dict(task)
            for task in tasks
            if (task_id := _text(task.get("task_id"))) is not None
        }
        placeholders = frozenset(
            task_id
            for task_id, task in source_tasks.items()
            if _boolean(task.get("is_placeholder"))
        )
        done = frozenset(
            task_id
            for task_id, task in source_tasks.items()
            if (_text(task.get("status")) or "").casefold() == COMPLETED_STATUS
            and task_id not in placeholders
        )
        excluded = frozenset(str(task_id) for task_id in excluded_task_ids)
        eligible = frozenset(
            task_id
            for task_id, task in source_tasks.items()
            if task_id not in placeholders
            and task_id not in excluded
            and (_text(task.get("status")) or "").casefold() in PLANNABLE_STATUSES
        )
        committed = frozenset(str(task_id) for task_id in committed_task_ids) & eligible

        memberships: dict[str, set[str]] = {}
        reliable: set[str] = set()
        unreliable: set[str] = set()
        for task_id, task in source_tasks.items():
            initiative_id = _text(task.get("initiative_id"))
            if initiative_id is None:
                continue
            memberships.setdefault(initiative_id, set()).add(task_id)
            status = (_text(task.get("status")) or "").casefold()
            if task_id in placeholders or status not in PLANNABLE_STATUSES | {COMPLETED_STATUS}:
                unreliable.add(initiative_id)
            else:
                reliable.add(initiative_id)

        graph: dict[str, set[str]] = {task_id: set() for task_id in source_tasks}
        for dependency in dependencies:
            blocker = _text(dependency.get("blocking_task_id"))
            blocked = _text(dependency.get("blocked_task_id"))
            if blocker is not None and blocked is not None:
                graph.setdefault(blocker, set()).add(blocked)

        rung_values = {
            task_id: rung
            for task_id in eligible
            if (rung := _number(source_tasks[task_id].get("rung"))) is not None
        }
        deadlines = {
            task_id: ordinal
            for task_id in eligible
            if (ordinal := date_ordinal(source_tasks[task_id].get("planned_end"))) is not None
        }
        return cls(
            done_task_ids=done,
            committed_task_ids=committed,
            remaining_task_ids=eligible - committed,
            eligible_task_ids=eligible,
            excluded_task_ids=excluded,
            placeholder_task_ids=placeholders,
            initiative_membership={
                initiative_id: frozenset(task_ids)
                for initiative_id, task_ids in memberships.items()
            },
            reliable_initiative_ids=frozenset(reliable - unreliable),
            dependency_graph={
                task_id: frozenset(blocked_ids) for task_id, blocked_ids in graph.items()
            },
            rung_values=rung_values,
            planned_end_ordinals=deadlines,
            source_tasks=source_tasks,
        )

    def with_committed(self, task_id: str) -> "PlanningContext":
        if task_id not in self.eligible_task_ids:
            return self
        committed = self.committed_task_ids | {task_id}
        return PlanningContext(
            done_task_ids=self.done_task_ids,
            committed_task_ids=committed,
            remaining_task_ids=self.eligible_task_ids - committed,
            eligible_task_ids=self.eligible_task_ids,
            excluded_task_ids=self.excluded_task_ids,
            placeholder_task_ids=self.placeholder_task_ids,
            initiative_membership=self.initiative_membership,
            reliable_initiative_ids=self.reliable_initiative_ids,
            dependency_graph=self.dependency_graph,
            rung_values=self.rung_values,
            planned_end_ordinals=self.planned_end_ordinals,
            source_tasks=self.source_tasks,
        )


def _normalized_score(value: float | int | None, values: Iterable[float | int], weight: float) -> float:
    if value is None:
        return 0.0
    all_values = tuple(values)
    if not all_values:
        return 0.0
    lowest = min(all_values)
    highest = max(all_values)
    if math.isclose(float(lowest), float(highest)):
        return weight
    return weight * (float(value) - float(lowest)) / (float(highest) - float(lowest))


def _initiative_completion_score(
    task: Mapping[str, Any], context: PlanningContext, config: ScoringConfig
) -> float:
    task_id = _text(task.get("task_id")) or ""
    initiative_id = _text(task.get("initiative_id"))
    if (
        not initiative_id
        or initiative_id not in context.reliable_initiative_ids
        or task_id in context.placeholder_task_ids
    ):
        return 0.0
    members = context.initiative_membership.get(initiative_id, frozenset())
    unfinished_after_task = members - context.done_task_ids - context.committed_task_ids - {task_id}
    return config.initiative_completion_weight / (len(unfinished_after_task) + 1)


def downstream_task_ids(task_id: str, context: PlanningContext) -> frozenset[str]:
    """Return unique reachable, unfinished, non-placeholder descendants safely."""

    reachable: set[str] = set()
    visited = {task_id}
    stack = list(context.dependency_graph.get(task_id, ()))
    while stack:
        descendant = stack.pop()
        if descendant in visited:
            continue
        visited.add(descendant)
        if descendant in context.placeholder_task_ids:
            continue
        if descendant in context.excluded_task_ids:
            continue
        if descendant in context.remaining_task_ids:
            reachable.add(descendant)
        stack.extend(context.dependency_graph.get(descendant, ()))
    return frozenset(reachable)


def _dependency_unlock_score(
    task_id: str, context: PlanningContext, config: ScoringConfig
) -> float:
    downstream_count = len(downstream_task_ids(task_id, context))
    share = min(downstream_count / config.dependency_unlock_saturation_count, 1.0)
    return config.dependency_unlock_weight * share


def calculate_task_score(
    task: Mapping[str, Any],
    context: PlanningContext,
    config: ScoringConfig | None = None,
) -> TaskScore:
    """Calculate explainable business value without consulting feasibility state."""

    config = config or ScoringConfig()
    task_id = _text(task.get("task_id")) or ""
    status = (_text(task.get("status")) or "").casefold()
    components = {
        "rung_score": 0.0,
        "status_score": 0.0,
        "deadline_score": 0.0,
        "initiative_completion_score": 0.0,
        "dependency_unlock_score": 0.0,
    }
    if task_id in context.placeholder_task_ids:
        return TaskScore(task_id, 0.0, 0.0, 0.0, components, eligible=False,
                         not_eligible_reason="PLACEHOLDER_TASK")
    if status == COMPLETED_STATUS:
        return TaskScore(task_id, 0.0, 0.0, 0.0, components, eligible=False,
                         not_eligible_reason="ALREADY_COMPLETED")
    if task_id in context.excluded_task_ids:
        return TaskScore(task_id, 0.0, 0.0, 0.0, components, eligible=False,
                         not_eligible_reason="ELIGIBILITY_DATA_CONSTRAINT")
    if task_id not in context.eligible_task_ids:
        return TaskScore(task_id, 0.0, 0.0, 0.0, components, eligible=False,
                         not_eligible_reason="UNSUPPORTED_STATUS")

    rung = context.rung_values.get(task_id)
    rung_values = tuple(context.rung_values.values())
    rung_score = _normalized_score(rung, rung_values, config.rung_weight)
    components.update(
        rung_score=rung_score,
        status_score=config.in_progress_bonus if status == "inprogress" else 0.0,
        deadline_score=0.0,
        initiative_completion_score=_initiative_completion_score(task, context, config),
        dependency_unlock_score=_dependency_unlock_score(task_id, context, config),
    )
    components = {name: round(value, 6) for name, value in components.items()}
    base_score = sum(components[name] for name in ("rung_score", "status_score"))
    dynamic_score = sum(
        components[name]
        for name in ("initiative_completion_score", "dependency_unlock_score")
    )
    return TaskScore(
        task_id=task_id,
        total_score=round(base_score + dynamic_score, 6),
        base_score=round(base_score, 6),
        dynamic_score=round(dynamic_score, 6),
        components=components,
    )


def score_sort_key(
    task: Mapping[str, Any],
    score: TaskScore,
    *,
    use_planned_start: bool = True,
) -> tuple[Any, ...]:
    """Return the absolute business-priority key for one comparable task set.

    ``rung`` is deliberately outside the additive score so no combination of
    status, initiative, or dependency bonuses can overtake a higher rung.
    Callers disable the date component when any task in the equal-rung set has
    no usable ``planned_start``; that keeps a missing date from becoming an
    invented earliest or latest value.
    """

    status = (_text(task.get("status")) or "").casefold()
    rung = _number(task.get("rung"))
    planned_start = date_ordinal(task.get("planned_start"))
    return (
        -(rung if rung is not None else 0.0),
        planned_start if use_planned_start and planned_start is not None else 0,
        -score.total_score,
        0 if status == "inprogress" else 1,
        _text(task.get("task_id")) or "",
    )
