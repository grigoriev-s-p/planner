from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .analytics import cross_team_links, reserve_candidates, role_coverage, skill_coverage
from .models import EngineerAllocation, EngineerBucket


class StarMapService:
    def __init__(
        self,
        engineers: Iterable[dict[str, Any]],
        engineer_skills: Iterable[dict[str, Any]],
        tasks: Iterable[dict[str, Any]],
        current_sprint: int,
    ):
        self.engineer_rows = tuple(engineers)
        self.skill_rows = tuple(engineer_skills)
        self.tasks = tuple(tasks)
        self.current_sprint = current_sprint
        self.buckets = self._build_buckets()
        self.allocations = self._build_allocations()
        self.skills_by_engineer = self._build_skills()
        self.profiles = self._build_profiles()

    def payload(self) -> dict[str, Any]:
        return {
            "current_sprint": self.current_sprint,
            "teams": self._teams(),
            "engineers": self._engineers(),
            "skills": sorted(
                {technology for values in self.skills_by_engineer.values() for technology in values},
                key=str.casefold,
            ),
            "roles": sorted(
                {bucket.role for bucket in self.buckets},
                key=str.casefold,
            ),
            "cross_team_flows": self._cross_team_flows(),
            "bus_factor": None,
            "bus_factor_status": "NOT_IMPLEMENTED",
        }

    def _build_buckets(self) -> tuple[EngineerBucket, ...]:
        buckets = []
        seen = set()
        for row in self.engineer_rows:
            engineer_id = self._text(row.get("engineer_id"))
            team_id = self._text(row.get("team_id"))
            role = self._text(row.get("role"))
            rate = self._number(row.get("capacity_rate"))
            if not engineer_id or not team_id or not role or rate is None or rate <= 0:
                continue
            key = (engineer_id, team_id)
            if key in seen:
                continue
            seen.add(key)
            buckets.append(
                EngineerBucket(
                    engineer_id=engineer_id,
                    team_id=team_id,
                    role=role,
                    grade=self._text(row.get("grade")),
                    capacity_rate=rate,
                    capacity_hours=round(80.0 * rate, 2),
                )
            )
        return tuple(sorted(buckets, key=lambda item: (item.team_id, item.engineer_id)))

    def _build_allocations(self) -> tuple[EngineerAllocation, ...]:
        allocations = []
        for task in self.tasks:
            task_id = self._text(task.get("task_id"))
            if not task_id:
                continue
            for row in task.get("allocations") or []:
                engineer_id = self._text(row.get("engineer_id"))
                role = self._text(row.get("role"))
                source_team_id = self._text(row.get("source_team_id"))
                target_team_id = self._text(row.get("assigned_task_team_id"))
                sprint = self._integer(row.get("sprint"))
                hours = self._number(row.get("allocated_hours"))
                if not engineer_id or not role or not source_team_id or not target_team_id or sprint is None or hours is None:
                    continue
                allocations.append(
                    EngineerAllocation(
                        task_id=task_id,
                        engineer_id=engineer_id,
                        role=role,
                        source_team_id=source_team_id,
                        assigned_task_team_id=target_team_id,
                        sprint=sprint,
                        allocated_hours=hours,
                        cross_team=bool(row.get("cross_team")),
                    )
                )
        return tuple(allocations)

    def _build_skills(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for row in self.skill_rows:
            engineer_id = self._text(row.get("engineer_id"))
            technology = self._text(row.get("technology"))
            if engineer_id and technology:
                result[engineer_id].add(technology)
        return result

    def _build_profiles(self) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        allocations_by_engineer: dict[str, list[EngineerAllocation]] = defaultdict(list)
        for allocation in self.allocations:
            if allocation.sprint == self.current_sprint:
                allocations_by_engineer[allocation.engineer_id].append(allocation)
        for bucket in self.buckets:
            target = profiles.setdefault(
                bucket.engineer_id,
                {
                    "roles": set(),
                    "grades": set(),
                    "team_ids": set(),
                    "buckets": [],
                    "skills": set(),
                },
            )
            target["roles"].add(bucket.role)
            target["team_ids"].add(bucket.team_id)
            if bucket.grade:
                target["grades"].add(bucket.grade)
            target["buckets"].append(bucket)
        for engineer_id, profile in profiles.items():
            profile["skills"] = set(self.skills_by_engineer.get(engineer_id, set()))
            used_by_team: dict[str, float] = defaultdict(float)
            for allocation in allocations_by_engineer.get(engineer_id, []):
                used_by_team[allocation.source_team_id] += allocation.allocated_hours
            bucket_payload = []
            for bucket in sorted(profile["buckets"], key=lambda item: item.team_id):
                used = used_by_team[bucket.team_id]
                bucket_payload.append(
                    {
                        "team_id": bucket.team_id,
                        "role": bucket.role,
                        "capacity_rate": bucket.capacity_rate,
                        "capacity_hours": bucket.capacity_hours,
                        "used_hours": round(used, 2),
                        "free_hours": round(max(bucket.capacity_hours - used, 0.0), 2),
                    }
                )
            profile["bucket_payload"] = bucket_payload
            profile["capacity_hours"] = round(sum(item["capacity_hours"] for item in bucket_payload), 2)
            profile["used_hours"] = round(sum(item["used_hours"] for item in bucket_payload), 2)
            profile["free_hours"] = round(sum(item["free_hours"] for item in bucket_payload), 2)
            profile["grade"] = ", ".join(sorted(profile["grades"])) or None
        return profiles

    def _teams(self) -> list[dict[str, Any]]:
        team_ids = sorted({bucket.team_id for bucket in self.buckets})
        output = []
        for team_id in team_ids:
            team_buckets = [bucket for bucket in self.buckets if bucket.team_id == team_id]
            member_ids = sorted({bucket.engineer_id for bucket in team_buckets})
            capacity = round(sum(bucket.capacity_hours for bucket in team_buckets), 2)
            used = round(
                sum(
                    allocation.allocated_hours
                    for allocation in self.allocations
                    if allocation.sprint == self.current_sprint and allocation.source_team_id == team_id
                ),
                2,
            )
            coverage = skill_coverage(member_ids, self.skills_by_engineer)
            roles = role_coverage(team_id, self.buckets, self.allocations, self.current_sprint)
            links = cross_team_links(team_id, self.allocations, self.current_sprint)
            output.append(
                {
                    "team_id": team_id,
                    "member_ids": member_ids,
                    "member_count": len(member_ids),
                    "technology_count": len(coverage),
                    "capacity_hours": capacity,
                    "used_hours": used,
                    "free_hours": round(max(capacity - used, 0.0), 2),
                    "skill_coverage": [
                        {
                            "technology": item.technology,
                            "engineer_ids": list(item.engineer_ids),
                            "engineer_count": item.member_count,
                            "coverage_pct": item.coverage_pct,
                        }
                        for item in coverage
                    ],
                    "role_coverage": [
                        {
                            "role": item.role,
                            "engineer_ids": list(item.engineer_ids),
                            "engineer_count": item.engineer_count,
                            "capacity_hours": item.capacity_hours,
                            "used_hours": item.used_hours,
                            "free_hours": item.free_hours,
                        }
                        for item in roles
                    ],
                    "cross_team_links": links,
                    "cross_team_outgoing_hours": round(
                        sum(item["hours"] for item in links if item["direction"] == "outgoing"),
                        2,
                    ),
                    "cross_team_incoming_hours": round(
                        sum(item["hours"] for item in links if item["direction"] == "incoming"),
                        2,
                    ),
                }
            )
        return output

    def _engineers(self) -> list[dict[str, Any]]:
        current_allocations: dict[str, list[EngineerAllocation]] = defaultdict(list)
        for allocation in self.allocations:
            if allocation.sprint == self.current_sprint:
                current_allocations[allocation.engineer_id].append(allocation)
        output = []
        for engineer_id, profile in sorted(self.profiles.items()):
            reserves = reserve_candidates(
                engineer_id=engineer_id,
                role_names=set(profile["roles"]),
                skills=set(profile["skills"]),
                team_ids=set(profile["team_ids"]),
                engineer_profiles=self.profiles,
            )
            output.append(
                {
                    "engineer_id": engineer_id,
                    "roles": sorted(profile["roles"]),
                    "grade": profile["grade"],
                    "team_ids": sorted(profile["team_ids"]),
                    "skills": sorted(profile["skills"], key=str.casefold),
                    "capacity_buckets": profile["bucket_payload"],
                    "capacity_hours": profile["capacity_hours"],
                    "used_hours": profile["used_hours"],
                    "free_hours": profile["free_hours"],
                    "allocations": [
                        {
                            "task_id": item.task_id,
                            "role": item.role,
                            "source_team_id": item.source_team_id,
                            "assigned_task_team_id": item.assigned_task_team_id,
                            "allocated_hours": item.allocated_hours,
                            "cross_team": item.cross_team,
                        }
                        for item in current_allocations.get(engineer_id, [])
                    ],
                    "reserve_candidates": reserves,
                }
            )
        return output


    def _cross_team_flows(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for allocation in self.allocations:
            if allocation.sprint != self.current_sprint or not allocation.cross_team:
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
            target["hours"] += allocation.allocated_hours
            target["engineer_ids"].add(allocation.engineer_id)
            target["task_ids"].add(allocation.task_id)
        return [
            {
                "source_team_id": source,
                "target_team_id": target,
                "hours": round(payload["hours"], 2),
                "engineer_ids": sorted(payload["engineer_ids"]),
                "task_ids": sorted(payload["task_ids"]),
            }
            for (source, target), payload in sorted(grouped.items())
        ]

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _integer(cls, value: Any) -> int | None:
        number = cls._number(value)
        return int(number) if number is not None else None
