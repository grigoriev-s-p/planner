from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineerBucket:
    engineer_id: str
    team_id: str
    role: str
    grade: str | None
    capacity_rate: float
    capacity_hours: float


@dataclass(frozen=True)
class EngineerAllocation:
    task_id: str
    engineer_id: str
    role: str
    source_team_id: str
    assigned_task_team_id: str
    sprint: int
    allocated_hours: float
    cross_team: bool


@dataclass(frozen=True)
class SkillCoverage:
    technology: str
    engineer_ids: tuple[str, ...]
    member_count: int
    coverage_pct: float


@dataclass(frozen=True)
class RoleCoverage:
    role: str
    engineer_ids: tuple[str, ...]
    engineer_count: int
    capacity_hours: float
    used_hours: float
    free_hours: float
