from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import EngineerAllocation, EngineerBucket, RoleCoverage, SkillCoverage


def skill_coverage(
    member_ids: Iterable[str],
    skills_by_engineer: dict[str, set[str]],
) -> list[SkillCoverage]:
    members = tuple(sorted(set(member_ids)))
    if not members:
        return []
    engineers_by_skill: dict[str, set[str]] = defaultdict(set)
    for engineer_id in members:
        for technology in skills_by_engineer.get(engineer_id, set()):
            engineers_by_skill[technology].add(engineer_id)
    return sorted(
        (
            SkillCoverage(
                technology=technology,
                engineer_ids=tuple(sorted(engineer_ids)),
                member_count=len(engineer_ids),
                coverage_pct=round(len(engineer_ids) / len(members) * 100, 2),
            )
            for technology, engineer_ids in engineers_by_skill.items()
        ),
        key=lambda item: (-item.member_count, item.technology.casefold()),
    )


def role_coverage(
    team_id: str,
    buckets: Iterable[EngineerBucket],
    allocations: Iterable[EngineerAllocation],
    sprint: int,
) -> list[RoleCoverage]:
    team_buckets = [bucket for bucket in buckets if bucket.team_id == team_id]
    used_by_engineer: dict[str, float] = defaultdict(float)
    for allocation in allocations:
        if allocation.sprint == sprint and allocation.source_team_id == team_id:
            used_by_engineer[allocation.engineer_id] += allocation.allocated_hours
    buckets_by_role: dict[str, list[EngineerBucket]] = defaultdict(list)
    for bucket in team_buckets:
        buckets_by_role[bucket.role].append(bucket)
    result = []
    for role, role_buckets in buckets_by_role.items():
        engineer_ids = tuple(sorted({bucket.engineer_id for bucket in role_buckets}))
        capacity = sum(bucket.capacity_hours for bucket in role_buckets)
        used = sum(used_by_engineer[engineer_id] for engineer_id in engineer_ids)
        result.append(
            RoleCoverage(
                role=role,
                engineer_ids=engineer_ids,
                engineer_count=len(engineer_ids),
                capacity_hours=round(capacity, 2),
                used_hours=round(used, 2),
                free_hours=round(max(capacity - used, 0.0), 2),
            )
        )
    return sorted(result, key=lambda item: (-item.engineer_count, item.role.casefold()))


def cross_team_links(
    team_id: str,
    allocations: Iterable[EngineerAllocation],
    sprint: int,
) -> list[dict]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for allocation in allocations:
        if allocation.sprint != sprint or not allocation.cross_team:
            continue
        if team_id not in {allocation.source_team_id, allocation.assigned_task_team_id}:
            continue
        key = (allocation.source_team_id, allocation.assigned_task_team_id)
        target = grouped.setdefault(
            key,
            {
                "source_team_id": allocation.source_team_id,
                "target_team_id": allocation.assigned_task_team_id,
                "hours": 0.0,
                "engineer_ids": set(),
                "task_ids": set(),
            },
        )
        target["hours"] = float(target["hours"]) + allocation.allocated_hours
        target["engineer_ids"].add(allocation.engineer_id)
        target["task_ids"].add(allocation.task_id)
    return [
        {
            "source_team_id": source,
            "target_team_id": target,
            "direction": "outgoing" if source == team_id else "incoming",
            "hours": round(float(payload["hours"]), 2),
            "engineer_ids": sorted(payload["engineer_ids"]),
            "task_ids": sorted(payload["task_ids"]),
        }
        for (source, target), payload in sorted(grouped.items())
    ]


def reserve_candidates(
    engineer_id: str,
    role_names: set[str],
    skills: set[str],
    team_ids: set[str],
    engineer_profiles: dict[str, dict],
    limit: int = 5,
) -> list[dict]:
    candidates = []
    for candidate_id, profile in engineer_profiles.items():
        if candidate_id == engineer_id:
            continue
        candidate_roles = set(profile["roles"])
        shared_roles = sorted(role_names & candidate_roles)
        if not shared_roles:
            continue
        candidate_skills = set(profile["skills"])
        shared_skills = sorted(skills & candidate_skills)
        if not shared_skills:
            continue
        shared_teams = sorted(team_ids & set(profile["team_ids"]))
        candidates.append(
            {
                "engineer_id": candidate_id,
                "roles": sorted(candidate_roles),
                "grade": profile["grade"],
                "team_ids": sorted(profile["team_ids"]),
                "shared_roles": shared_roles,
                "shared_skills": shared_skills,
                "shared_skill_count": len(shared_skills),
                "same_team": bool(shared_teams),
                "free_hours": round(float(profile["free_hours"]), 2),
            }
        )
    candidates.sort(
        key=lambda item: (
            not item["same_team"],
            -item["shared_skill_count"],
            -item["free_hours"],
            item["engineer_id"],
        )
    )
    return candidates[:limit]
