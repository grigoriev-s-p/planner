"""Deterministic task-level diff between two immutable plan versions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import PlanItem, QuarterPlan


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    SPRINT_CHANGED = "sprint_changed"
    STATE_CHANGED = "state_changed"
    STORY_POINTS_CHANGED = "story_points_changed"
    STATUS_CHANGED = "status_changed"
    REASON_CHANGED = "reason_changed"
    TEAM_CHANGED = "team_changed"
    INITIATIVE_CHANGED = "initiative_changed"
    ALLOCATIONS_CHANGED = "allocations_changed"


@dataclass(frozen=True, slots=True)
class PlanDiffEntry:
    task_id: str
    initiative_id: str
    changes: tuple[ChangeType, ...]
    before: PlanItem | None
    after: PlanItem | None


@dataclass(frozen=True, slots=True)
class PlanDiff:
    from_version: int
    to_version: int
    entries: tuple[PlanDiffEntry, ...]

    def by_change(self, change: ChangeType) -> tuple[PlanDiffEntry, ...]:
        return tuple(entry for entry in self.entries if change in entry.changes)


def compare_plans(before: QuarterPlan, after: QuarterPlan) -> PlanDiff:
    if before.quarter_id != after.quarter_id:
        raise ValueError("cannot compare plans from different quarters")
    before_by_id = before.by_task_id()
    after_by_id = after.by_task_id()
    entries: list[PlanDiffEntry] = []

    for task_id in sorted(set(before_by_id) | set(after_by_id)):
        old = before_by_id.get(task_id)
        new = after_by_id.get(task_id)
        if old is None:
            entries.append(
                PlanDiffEntry(task_id, new.initiative_id, (ChangeType.ADDED,), None, new)
            )
            continue
        if new is None:
            entries.append(
                PlanDiffEntry(task_id, old.initiative_id, (ChangeType.REMOVED,), old, None)
            )
            continue

        changes: list[ChangeType] = []
        comparisons = (
            (old.completion_sprint, new.completion_sprint, ChangeType.SPRINT_CHANGED),
            (old.state, new.state, ChangeType.STATE_CHANGED),
            (old.story_points, new.story_points, ChangeType.STORY_POINTS_CHANGED),
            (old.decision_status, new.decision_status, ChangeType.STATUS_CHANGED),
            (old.mismatch_reason, new.mismatch_reason, ChangeType.REASON_CHANGED),
            (old.team_id, new.team_id, ChangeType.TEAM_CHANGED),
            (old.initiative_id, new.initiative_id, ChangeType.INITIATIVE_CHANGED),
            (old.allocations, new.allocations, ChangeType.ALLOCATIONS_CHANGED),
        )
        changes.extend(change for left, right, change in comparisons if left != right)
        if changes:
            entries.append(
                PlanDiffEntry(task_id, new.initiative_id, tuple(changes), old, new)
            )

    return PlanDiff(before.version, after.version, tuple(entries))
