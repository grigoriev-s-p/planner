from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd


CompetencyType = Literal["skill", "role"]

ACTIVE_STATUSES = {
    "todo",
    "to do",
    "inprogress",
    "in progress",
}

ROLE_ALIASES = {
    "девопс": "Девопс",
    "девопс ": "Девопс",
    "devops": "Девопс",
    "разработчик ios": "Разработчик IOS",
    "разработчик ios ": "Разработчик IOS",
    "разработчик bigdata": "Разработчик Big Data",
    "разработчик big data": "Разработчик Big Data",
    "руководитель bigdata": "Руководитель BigData",
}

SKILL_ALIASES = {
    "apache kafka": "Kafka",
    "kafka": "Kafka",
    "microservices": "Microservices",
    "микросервисы": "Microservices",
    "microservices архитектура": "Microservices",
    "архитектура микросервисов": "Microservices",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "javascript": "JavaScript",
    "java script": "JavaScript",
    "typescript": "TypeScript",
    "ios": "iOS",
    "android": "Android",
    "c#": "C#",
}

GRADE_ORDER = {
    "junior": 1,
    "middle": 2,
    "senior": 3,
    "lead": 4,
}


def _clean_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def normalize_role(value) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    return ROLE_ALIASES.get(cleaned.casefold(), cleaned)


def normalize_skill(value) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    return SKILL_ALIASES.get(cleaned.casefold(), cleaned)


def _pick_column(df: pd.DataFrame, candidates: Iterable[str], required: bool = True) -> str | None:
    normalized = {str(col).strip().casefold(): col for col in df.columns}

    for candidate in candidates:
        found = normalized.get(candidate.casefold())
        if found is not None:
            return found

    if required:
        raise ValueError(
            f"Не найдена ни одна из колонок {list(candidates)}. "
            f"Есть колонки: {list(df.columns)}"
        )
    return None


def _status_is_active(value) -> bool:
    return _clean_text(value).casefold() in ACTIVE_STATUSES


def _grade_similarity(a, b) -> float:
    ga = GRADE_ORDER.get(_clean_text(a).casefold())
    gb = GRADE_ORDER.get(_clean_text(b).casefold())

    if ga is None or gb is None:
        return 0.5

    distance = abs(ga - gb)
    return {
        0: 1.0,
        1: 0.75,
        2: 0.4,
    }.get(distance, 0.2)


def _role_similarity(a, b) -> float:
    ra = normalize_role(a).casefold()
    rb = normalize_role(b).casefold()

    if not ra or not rb:
        return 0.0
    if ra == rb:
        return 1.0

    developer_markers = ("разработчик", "developer")
    analyst_markers = ("аналитик", "analyst")
    test_markers = ("тестировщик", "qa")
    architecture_markers = ("архитектор", "architect")

    for group in (
        developer_markers,
        analyst_markers,
        test_markers,
        architecture_markers,
    ):
        if any(x in ra for x in group) and any(x in rb for x in group):
            return 0.65

    return 0.0


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass(frozen=True)
class BusFactorPaths:
    processed_dir: Path

    @property
    def engineers(self) -> Path:
        return self.processed_dir / "engineers.csv"

    @property
    def engineer_skills(self) -> Path:
        return self.processed_dir / "engineer_skills.csv"

    @property
    def tasks(self) -> Path:
        return self.processed_dir / "tasks.csv"

    @property
    def task_role_effort(self) -> Path:
        return self.processed_dir / "task_role_effort.csv"

    @property
    def task_skill_requirements(self) -> Path:
        # Не является обязательным файлом текущего проекта.
        # Если в будущем появится явная связь task -> required_skill,
        # Bus Factor автоматически сможет использовать её.
        return self.processed_dir / "task_skill_requirements.csv"


class BusFactorService:
    """
    Аналитический модуль Bus Factor.

    Он НЕ:
    - планирует задачи;
    - вычисляет свободные часы в спринте;
    - назначает cross-team инженеров.

    Он:
    - считает BF по ролям и skills;
    - сравнивает BF команды и организации;
    - добавляет контекст квартального спроса;
    - ищет потенциальных помощников из других команд;
    - предлагает кандидатов на обучение;
    - моделирует эффект отсутствия сотрудника.

    Главный контракт для основной части проекта:
        get_potential_helpers(...)
    Эта функция возвращает только строки потенциальных помощников.
    """

    def __init__(
        self,
        engineers: pd.DataFrame,
        engineer_skills: pd.DataFrame,
        tasks: pd.DataFrame | None = None,
        task_role_effort: pd.DataFrame | None = None,
        task_skill_requirements: pd.DataFrame | None = None,
    ):
        self.engineers = self._prepare_engineers(engineers)
        self.engineer_skills = self._prepare_engineer_skills(engineer_skills)
        self.tasks = self._prepare_tasks(tasks)
        self.task_role_effort = self._prepare_role_demand(task_role_effort)
        self.task_skill_requirements = self._prepare_skill_demand(
            task_skill_requirements
        )

        self.role_demand = self._build_role_demand()
        self.skill_demand = self._build_skill_demand()

    @classmethod
    def from_processed_dir(
        cls,
        processed_dir: str | Path = "data/processed",
    ) -> "BusFactorService":
        paths = BusFactorPaths(Path(processed_dir))

        for required in (paths.engineers, paths.engineer_skills):
            if not required.exists():
                raise FileNotFoundError(
                    f"Не найден обязательный файл {required}. "
                    "Сначала запусти preprocessing проекта."
                )

        engineers = pd.read_csv(paths.engineers)
        engineer_skills = pd.read_csv(paths.engineer_skills)

        tasks = pd.read_csv(paths.tasks) if paths.tasks.exists() else None
        role_effort = (
            pd.read_csv(paths.task_role_effort)
            if paths.task_role_effort.exists()
            else None
        )
        skill_requirements = (
            pd.read_csv(paths.task_skill_requirements)
            if paths.task_skill_requirements.exists()
            else None
        )

        return cls(
            engineers=engineers,
            engineer_skills=engineer_skills,
            tasks=tasks,
            task_role_effort=role_effort,
            task_skill_requirements=skill_requirements,
        )

    @staticmethod
    def _prepare_engineers(df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()

        engineer_col = _pick_column(data, ["engineer_id", "employee_id"])
        team_col = _pick_column(data, ["team_id", "team"])
        role_col = _pick_column(data, ["role", "role_name"])
        grade_col = _pick_column(data, ["grade"], required=False)
        capacity_col = _pick_column(
            data,
            ["capacity_rate", "fte", "capacity"],
            required=False,
        )

        rename = {
            engineer_col: "engineer_id",
            team_col: "team_id",
            role_col: "role",
        }
        if grade_col:
            rename[grade_col] = "grade"
        if capacity_col:
            rename[capacity_col] = "capacity_rate"

        data = data.rename(columns=rename)

        if "grade" not in data:
            data["grade"] = ""
        if "capacity_rate" not in data:
            data["capacity_rate"] = 1.0

        data = data[
            ["engineer_id", "team_id", "role", "grade", "capacity_rate"]
        ].copy()

        data["engineer_id"] = data["engineer_id"].map(_clean_text)
        data["team_id"] = data["team_id"].map(_clean_text)
        data["role"] = data["role"].map(normalize_role)
        data["grade"] = data["grade"].map(_clean_text)
        data["capacity_rate"] = pd.to_numeric(
            data["capacity_rate"], errors="coerce"
        ).fillna(0.0)

        data = data[
            (data["engineer_id"] != "")
            & (data["team_id"] != "")
        ]

        return data.drop_duplicates().reset_index(drop=True)

    @staticmethod
    def _prepare_engineer_skills(df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()

        engineer_col = _pick_column(data, ["engineer_id", "employee_id"])
        skill_col = _pick_column(
            data,
            ["skill", "skill_name", "technology", "competency"],
        )

        data = data.rename(
            columns={
                engineer_col: "engineer_id",
                skill_col: "skill",
            }
        )[["engineer_id", "skill"]]

        data["engineer_id"] = data["engineer_id"].map(_clean_text)
        data["skill"] = data["skill"].map(normalize_skill)

        data = data[
            (data["engineer_id"] != "")
            & (data["skill"] != "")
        ]

        return data.drop_duplicates().reset_index(drop=True)

    @staticmethod
    def _prepare_tasks(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None or df.empty:
            return None

        data = df.copy()
        task_col = _pick_column(data, ["task_id"])
        team_col = _pick_column(
            data,
            ["team_id", "team", "assigned_team_id"],
            required=False,
        )
        status_col = _pick_column(data, ["status"], required=False)

        rename = {task_col: "task_id"}
        if team_col:
            rename[team_col] = "team_id"
        if status_col:
            rename[status_col] = "status"

        data = data.rename(columns=rename)
        if "team_id" not in data:
            data["team_id"] = ""
        if "status" not in data:
            data["status"] = ""

        data = data[["task_id", "team_id", "status"]].copy()
        data["task_id"] = data["task_id"].map(_clean_text)
        data["team_id"] = data["team_id"].map(_clean_text)
        data["status"] = data["status"].map(_clean_text)

        return data.drop_duplicates("task_id").reset_index(drop=True)

    @staticmethod
    def _prepare_role_demand(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None or df.empty:
            return None

        data = df.copy()

        task_col = _pick_column(data, ["task_id"])
        role_col = _pick_column(
            data,
            ["role", "role_name", "required_role"],
        )
        hours_col = _pick_column(
            data,
            [
                "required_hours",
                "required_hh",
                "remaining_hh",
                "effort_hh",
                "hours",
                "estimated_hh",
                "role_hours",
            ],
        )

        data = data.rename(
            columns={
                task_col: "task_id",
                role_col: "role",
                hours_col: "required_hours",
            }
        )[["task_id", "role", "required_hours"]]

        data["task_id"] = data["task_id"].map(_clean_text)
        data["role"] = data["role"].map(normalize_role)
        data["required_hours"] = pd.to_numeric(
            data["required_hours"], errors="coerce"
        ).fillna(0.0)

        return data[
            (data["task_id"] != "")
            & (data["role"] != "")
            & (data["required_hours"] > 0)
        ].reset_index(drop=True)

    @staticmethod
    def _prepare_skill_demand(df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None or df.empty:
            return None

        data = df.copy()

        task_col = _pick_column(data, ["task_id"])
        skill_col = _pick_column(
            data,
            ["skill", "required_skill", "skill_name", "technology"],
        )
        hours_col = _pick_column(
            data,
            ["required_hh", "required_hours", "hours"],
            required=False,
        )

        rename = {
            task_col: "task_id",
            skill_col: "skill",
        }
        if hours_col:
            rename[hours_col] = "required_hours"

        data = data.rename(columns=rename)
        if "required_hours" not in data:
            data["required_hours"] = 0.0

        data = data[["task_id", "skill", "required_hours"]].copy()
        data["task_id"] = data["task_id"].map(_clean_text)
        data["skill"] = data["skill"].map(normalize_skill)
        data["required_hours"] = pd.to_numeric(
            data["required_hours"], errors="coerce"
        ).fillna(0.0)

        return data[
            (data["task_id"] != "")
            & (data["skill"] != "")
        ].reset_index(drop=True)

    def _active_tasks(self) -> pd.DataFrame | None:
        if self.tasks is None:
            return None

        active = self.tasks[self.tasks["status"].map(_status_is_active)].copy()

        # Если статусы в конкретной версии CSV отличаются и ничего не распознано,
        # не будем молча считать квартальный спрос нулём.
        if active.empty and not self.tasks.empty:
            return None

        return active

    def _build_role_demand(self) -> pd.DataFrame:
        columns = [
            "team_id",
            "role",
            "task_count",
            "required_hours",
        ]

        if self.task_role_effort is None:
            return pd.DataFrame(columns=columns)

        effort = self.task_role_effort.copy()
        active_tasks = self._active_tasks()

        if active_tasks is None:
            # Можно посчитать спрос организации, но не надёжно привязать его
            # к "активному кварталу".
            return pd.DataFrame(columns=columns)

        merged = effort.merge(
            active_tasks[["task_id", "team_id"]],
            on="task_id",
            how="inner",
        )

        if merged.empty:
            return pd.DataFrame(columns=columns)

        return (
            merged.groupby(["team_id", "role"], as_index=False)
            .agg(
                task_count=("task_id", "nunique"),
                required_hours=("required_hours", "sum"),
            )
            .sort_values(["team_id", "role"])
            .reset_index(drop=True)
        )

    def _build_skill_demand(self) -> pd.DataFrame:
        columns = [
            "team_id",
            "skill",
            "task_count",
            "required_hours",
        ]

        if self.task_skill_requirements is None:
            return pd.DataFrame(columns=columns)

        active_tasks = self._active_tasks()
        if active_tasks is None:
            return pd.DataFrame(columns=columns)

        merged = self.task_skill_requirements.merge(
            active_tasks[["task_id", "team_id"]],
            on="task_id",
            how="inner",
        )

        if merged.empty:
            return pd.DataFrame(columns=columns)

        return (
            merged.groupby(["team_id", "skill"], as_index=False)
            .agg(
                task_count=("task_id", "nunique"),
                required_hours=("required_hours", "sum"),
            )
            .sort_values(["team_id", "skill"])
            .reset_index(drop=True)
        )

    def teams(self) -> list[str]:
        return sorted(self.engineers["team_id"].dropna().unique().tolist())

    def _team_engineers(self, team_id: str) -> set[str]:
        return set(
            self.engineers.loc[
                self.engineers["team_id"] == team_id,
                "engineer_id",
            ]
        )

    def team_engineers(self, team_id: str) -> list[str]:
        """Return unique engineers belonging to a team under the BF model."""

        if team_id not in set(self.teams()):
            raise KeyError(f"Неизвестная команда: {team_id}")
        return sorted(self._team_engineers(team_id))

    def _organization_engineers_for_skill(self, skill: str) -> set[str]:
        skill = normalize_skill(skill)
        return set(
            self.engineer_skills.loc[
                self.engineer_skills["skill"] == skill,
                "engineer_id",
            ]
        )

    def _organization_engineers_for_role(self, role: str) -> set[str]:
        role = normalize_role(role)
        return set(
            self.engineers.loc[
                self.engineers["role"] == role,
                "engineer_id",
            ]
        )

    def _team_bf(self, team_id: str, competency_type: CompetencyType, competency: str) -> int:
        team_people = self._team_engineers(team_id)

        if competency_type == "skill":
            holders = self._organization_engineers_for_skill(competency)
        else:
            holders = self._organization_engineers_for_role(competency)

        return len(team_people & holders)

    def _org_bf(self, competency_type: CompetencyType, competency: str) -> int:
        if competency_type == "skill":
            return len(self._organization_engineers_for_skill(competency))
        return len(self._organization_engineers_for_role(competency))

    def competency_engineers(
        self,
        team_id: str,
        competency_type: CompetencyType,
        competency: str,
    ) -> list[str]:
        """Return the unique team members counted in a competency Bus Factor."""

        if team_id not in set(self.teams()):
            raise KeyError(f"Неизвестная команда: {team_id}")

        if competency_type == "skill":
            holders = self._organization_engineers_for_skill(competency)
        else:
            holders = self._organization_engineers_for_role(competency)

        return sorted(self._team_engineers(team_id) & holders)

    def organization_competency_engineers(
        self,
        competency_type: CompetencyType,
        competency: str,
    ) -> list[str]:
        """Return unique competency holders across the organization."""

        if competency_type == "skill":
            holders = self._organization_engineers_for_skill(competency)
        else:
            holders = self._organization_engineers_for_role(competency)
        return sorted(holders)

    def _quarter_demand(
        self,
        team_id: str,
        competency_type: CompetencyType,
        competency: str,
    ) -> tuple[str, int | None, float | None]:
        if competency_type == "role":
            role = normalize_role(competency)

            if self.task_role_effort is None or self.tasks is None:
                return "unknown", None, None

            subset = self.role_demand[
                (self.role_demand["team_id"] == team_id)
                & (self.role_demand["role"] == role)
            ]

            if subset.empty:
                return "not_required", 0, 0.0

            row = subset.iloc[0]
            return (
                "required",
                int(row["task_count"]),
                float(row["required_hours"]),
            )

        skill = normalize_skill(competency)

        if self.task_skill_requirements is None:
            # В текущем датасете нет надёжной task -> skill связи.
            return "unknown", None, None

        subset = self.skill_demand[
            (self.skill_demand["team_id"] == team_id)
            & (self.skill_demand["skill"] == skill)
        ]

        if subset.empty:
            return "not_required", 0, 0.0

        row = subset.iloc[0]
        return (
            "required",
            int(row["task_count"]),
            float(row["required_hours"]),
        )

    @staticmethod
    def _coverage_status(
        team_bf: int,
        organization_bf: int,
        quarter_relevance: str,
    ) -> str:
        if quarter_relevance == "not_required":
            return "structural_only"

        if team_bf <= 1 and organization_bf <= 1:
            return "critical_organizational_dependency"

        if team_bf <= 1 and organization_bf > 1:
            return "cross_team_backup_available"

        if team_bf == 2:
            return "limited_coverage"

        return "sufficient_coverage"

    def role_coverage(self, team_id: str) -> pd.DataFrame:
        all_roles = sorted(self.engineers["role"].dropna().unique().tolist())

        # Включаем и роли, которых нет в штате, но они нужны задачам команды.
        demanded = set(
            self.role_demand.loc[
                self.role_demand["team_id"] == team_id,
                "role",
            ]
        )
        all_roles = sorted(set(all_roles) | demanded)

        rows = []
        for role in all_roles:
            team_bf = self._team_bf(team_id, "role", role)
            org_bf = self._org_bf("role", role)
            relevance, tasks_count, required_hours = self._quarter_demand(
                team_id, "role", role
            )

            rows.append(
                {
                    "team_id": team_id,
                    "competency_type": "role",
                    "competency": role,
                    "team_bus_factor": team_bf,
                    "organization_bus_factor": org_bf,
                    "quarter_relevance": relevance,
                    "quarter_task_count": tasks_count,
                    "quarter_required_hours": required_hours,
                    "coverage_status": self._coverage_status(
                        team_bf,
                        org_bf,
                        relevance,
                    ),
                }
            )

        return pd.DataFrame(rows)

    def skill_coverage(self, team_id: str) -> pd.DataFrame:
        all_skills = sorted(
            self.engineer_skills["skill"].dropna().unique().tolist()
        )

        demanded = set(
            self.skill_demand.loc[
                self.skill_demand["team_id"] == team_id,
                "skill",
            ]
        )
        all_skills = sorted(set(all_skills) | demanded)

        rows = []
        for skill in all_skills:
            team_bf = self._team_bf(team_id, "skill", skill)
            org_bf = self._org_bf("skill", skill)
            relevance, tasks_count, required_hours = self._quarter_demand(
                team_id, "skill", skill
            )

            rows.append(
                {
                    "team_id": team_id,
                    "competency_type": "skill",
                    "competency": skill,
                    "team_bus_factor": team_bf,
                    "organization_bus_factor": org_bf,
                    "quarter_relevance": relevance,
                    "quarter_task_count": tasks_count,
                    "quarter_required_hours": required_hours,
                    "coverage_status": self._coverage_status(
                        team_bf,
                        org_bf,
                        relevance,
                    ),
                }
            )

        return pd.DataFrame(rows)

    def coverage(self, team_id: str) -> pd.DataFrame:
        return pd.concat(
            [
                self.role_coverage(team_id),
                self.skill_coverage(team_id),
            ],
            ignore_index=True,
        )

    def _person_skills(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for row in self.engineer_skills.itertuples(index=False):
            result.setdefault(row.engineer_id, set()).add(row.skill)
        return result

    def _person_meta(self) -> pd.DataFrame:
        # Один человек может иметь несколько team assignments, но role/grade
        # в исходных данных обычно одинаковые. Берём первую запись.
        return (
            self.engineers[
                ["engineer_id", "role", "grade"]
            ]
            .drop_duplicates("engineer_id")
            .set_index("engineer_id")
        )

    def get_potential_helpers(
        self,
        target_team_id: str,
        competency_type: CompetencyType,
        competency: str,
    ) -> pd.DataFrame:
        """
        ГЛАВНЫЙ КОНТРАКТ С ОСНОВНЫМ ПРОЕКТОМ.

        Возвращает ТОЛЬКО потенциально подходящих cross-team специалистов.

        Важно:
        - одна строка = один уникальный engineer_id;
        - если инженер распределён между несколькими внешними командами,
          source_team_id содержит их через ", ";
        - capacity_rate суммируется по внешним assignments и ограничивается 1.0;
        - свободные часы, фактическая загрузка и решение о назначении
          здесь НЕ рассчитываются.

        Колонки:
            target_team_id
            competency_type
            competency
            engineer_id
            source_team_id
            role
            grade
            capacity_rate
            match_score
            reason
        """
        if target_team_id not in set(self.teams()):
            raise KeyError(f"Неизвестная команда: {target_team_id}")

        if competency_type == "skill":
            competency = normalize_skill(competency)
            holders = self._organization_engineers_for_skill(competency)
        elif competency_type == "role":
            competency = normalize_role(competency)
            holders = self._organization_engineers_for_role(competency)
        else:
            raise ValueError("competency_type должен быть 'skill' или 'role'")

        target_people = self._team_engineers(target_team_id)

        # Если человек уже входит в target team, внешним helper его не считаем.
        external_people = holders - target_people

        columns = [
            "target_team_id",
            "competency_type",
            "competency",
            "engineer_id",
            "source_team_id",
            "role",
            "grade",
            "capacity_rate",
            "match_score",
            "reason",
        ]

        if not external_people:
            return pd.DataFrame(columns=columns)

        person_meta = self._person_meta()
        person_skills = self._person_skills()

        local_holders = holders & target_people

        local_roles = []
        local_grades = []
        local_skill_sets = []

        for engineer_id in local_holders:
            if engineer_id in person_meta.index:
                local_roles.append(person_meta.loc[engineer_id, "role"])
                local_grades.append(person_meta.loc[engineer_id, "grade"])
            local_skill_sets.append(person_skills.get(engineer_id, set()))

        # Берём все внешние assignments подходящих людей.
        external_assignments = self.engineers[
            self.engineers["engineer_id"].isin(external_people)
            & (self.engineers["team_id"] != target_team_id)
        ].copy()

        rows = []

        # Ключевая разница: группируем по engineer_id, а не выдаём
        # отдельную строку на каждый engineer-team bucket.
        for engineer_id, assignments in external_assignments.groupby(
            "engineer_id",
            sort=True,
        ):
            first = assignments.iloc[0]

            source_teams = sorted(
                {
                    str(team_id)
                    for team_id in assignments["team_id"].dropna()
                    if str(team_id).strip()
                }
            )

            # Один человек физически не становится "1.5 человека",
            # даже если в данных есть несколько assignments.
            capacity_rate = min(
                float(
                    pd.to_numeric(
                        assignments["capacity_rate"],
                        errors="coerce",
                    ).fillna(0.0).sum()
                ),
                1.0,
            )

            role = first["role"]
            grade = first["grade"]

            role_score = 0.5
            grade_score = 0.5
            skill_profile_score = 0.0

            if local_roles:
                role_score = max(
                    _role_similarity(role, local_role)
                    for local_role in local_roles
                )

            if local_grades:
                grade_score = max(
                    _grade_similarity(grade, local_grade)
                    for local_grade in local_grades
                )

            if local_skill_sets:
                candidate_skills = person_skills.get(engineer_id, set())
                skill_profile_score = max(
                    _jaccard(candidate_skills, local_skills)
                    for local_skills in local_skill_sets
                )

            if competency_type == "skill":
                # Сам требуемый skill уже совпадает.
                match_score = (
                    0.70
                    + 0.15 * role_score
                    + 0.10 * skill_profile_score
                    + 0.05 * grade_score
                )
                reason = (
                    f"Имеет skill '{competency}'; "
                    f"role similarity={role_score:.2f}; "
                    f"profile similarity={skill_profile_score:.2f}"
                )
            else:
                # Роль уже совпадает по определению holder-а.
                match_score = (
                    0.80
                    + 0.15 * skill_profile_score
                    + 0.05 * grade_score
                )
                reason = (
                    f"Имеет роль '{competency}'; "
                    f"profile similarity={skill_profile_score:.2f}"
                )

            rows.append(
                {
                    "target_team_id": target_team_id,
                    "competency_type": competency_type,
                    "competency": competency,
                    "engineer_id": engineer_id,
                    "source_team_id": ", ".join(source_teams),
                    "role": role,
                    "grade": grade,
                    "capacity_rate": capacity_rate,
                    "match_score": round(float(match_score), 4),
                    "reason": reason,
                }
            )

        if not rows:
            return pd.DataFrame(columns=columns)

        return (
            pd.DataFrame(rows)[columns]
            .sort_values(
                ["match_score", "capacity_rate", "engineer_id"],
                ascending=[False, False, True],
            )
            .reset_index(drop=True)
        )

    def get_all_potential_helpers(
        self,
        max_team_bf: int = 2,
    ) -> pd.DataFrame:
        """
        Возвращает helper-кандидатов только для осмысленных дефицитов.

        Правила:
        1. Roles:
           - роль должна реально требоваться активными задачами квартала;
           - Team BF <= max_team_bf;
           - BF=0 допустим: если роль нужна, но в команде её вообще нет,
             внешний специалист особенно полезен.

        2. Skills:
           - если есть явный task -> required_skill dataset:
               берём только quarter_relevance='required';
               BF может быть 0..max_team_bf.
           - если task -> skill связи НЕТ:
               нельзя считать любой отсутствующий skill проблемой;
               поэтому берём только skills, которые уже есть в команде,
               то есть Team BF >= 1, и при этом Team BF <= max_team_bf.

        Таким образом Team-Alpha больше не получает рекомендации по ARKit,
        .NET и другим случайным технологиям только потому, что они существуют
        где-то в организации.
        """
        frames = []

        for team_id in self.teams():
            coverage = self.coverage(team_id)

            # --- Roles ---
            roles = coverage[
                (coverage["competency_type"] == "role")
                & (coverage["quarter_relevance"] == "required")
                & (coverage["team_bus_factor"] <= max_team_bf)
            ]

            # --- Skills ---
            skills = coverage[
                (coverage["competency_type"] == "skill")
                & (coverage["team_bus_factor"] <= max_team_bf)
            ]

            if self.task_skill_requirements is None:
                # Нет подтверждённой связи task -> skill.
                # BF=0 здесь не означает проблему: команда может просто
                # вообще не использовать данную технологию.
                skills = skills[
                    skills["team_bus_factor"] >= 1
                ]
            else:
                # Если task -> skill требования когда-нибудь появятся,
                # можно безопасно включать и BF=0, но только если skill
                # реально нужен текущему кварталу.
                skills = skills[
                    skills["quarter_relevance"] == "required"
                ]

            relevant = pd.concat(
                [roles, skills],
                ignore_index=True,
            )

            for item in relevant.itertuples(index=False):
                helpers = self.get_potential_helpers(
                    target_team_id=team_id,
                    competency_type=item.competency_type,
                    competency=item.competency,
                )

                if helpers.empty:
                    continue

                # Полезно сразу приложить контекст риска к каждой строке.
                helpers = helpers.copy()
                helpers["team_bus_factor"] = int(
                    item.team_bus_factor
                )
                helpers["organization_bus_factor"] = int(
                    item.organization_bus_factor
                )
                helpers["quarter_relevance"] = (
                    item.quarter_relevance
                )
                helpers["quarter_task_count"] = (
                    item.quarter_task_count
                )
                helpers["quarter_required_hours"] = (
                    item.quarter_required_hours
                )
                helpers["coverage_status"] = (
                    item.coverage_status
                )

                frames.append(helpers)

        base_columns = [
            "target_team_id",
            "competency_type",
            "competency",
            "engineer_id",
            "source_team_id",
            "role",
            "grade",
            "capacity_rate",
            "match_score",
            "reason",
            "team_bus_factor",
            "organization_bus_factor",
            "quarter_relevance",
            "quarter_task_count",
            "quarter_required_hours",
            "coverage_status",
        ]

        if not frames:
            return pd.DataFrame(columns=base_columns)

        result = pd.concat(frames, ignore_index=True)

        return (
            result[base_columns]
            .sort_values(
                [
                    "target_team_id",
                    "competency_type",
                    "competency",
                    "match_score",
                    "capacity_rate",
                    "engineer_id",
                ],
                ascending=[True, True, True, False, False, True],
            )
            .reset_index(drop=True)
        )

    def training_candidates(
        self,
        team_id: str,
        skill: str,
        top_n: int = 3,
    ) -> pd.DataFrame:
        """
        Кандидаты на дообучение skill внутри команды.

        Правила актуальности:
        - если нет явной связи task -> required_skill, рассматриваем только
          уже существующие в команде, но уязвимые skills: Team BF = 1..2;
        - если task_skill_requirements есть, допускаем Team BF = 0..2,
          но только когда skill реально required в текущем квартале.

        Правила кандидата:
        - сотрудник ещё НЕ владеет target skill;
        - если роль такая же, нужен минимум 1 общий skill с holder-ом;
        - если роль смежная, нужны минимум 2 общих skills с holder-ом.

        Таким образом случайные сотрудники с почти нулевой близостью профиля
        больше не попадают в рекомендации.
        """
        columns = [
            "team_id",
            "skill",
            "engineer_id",
            "role",
            "grade",
            "capacity_rate",
            "training_score",
            "closest_holder",
            "shared_skill_count",
            "shared_skills",
            "profile_similarity",
            "role_similarity",
            "reason",
        ]

        if team_id not in set(self.teams()):
            raise KeyError(f"Неизвестная команда: {team_id}")

        skill = normalize_skill(skill)

        team_bf = self._team_bf(team_id, "skill", skill)
        relevance, _, _ = self._quarter_demand(
            team_id,
            "skill",
            skill,
        )

        if self.task_skill_requirements is None:
            # Без task -> skill связи BF=0 не означает дефицит:
            # команда может вообще не использовать эту технологию.
            if not (1 <= team_bf <= 2):
                return pd.DataFrame(columns=columns)
        else:
            # Если явные требования появились, BF=0 допустим,
            # но только для реально требуемого skill.
            if relevance != "required" or team_bf > 2:
                return pd.DataFrame(columns=columns)

        holders = self._organization_engineers_for_skill(skill)
        team_people = self._team_engineers(team_id)
        local_holders = holders & team_people

        # Для BF 1..2 основной эталон — holder внутри команды.
        # Для BF=0 (возможен только при явном task->skill mapping)
        # используем holders по организации.
        reference_holders = local_holders or holders

        if not reference_holders:
            return pd.DataFrame(columns=columns)

        person_skills = self._person_skills()
        person_meta = self._person_meta()

        # Один человек может иметь несколько assignment-строк.
        # Для training recommendations нужен именно человек, а не bucket.
        team_assignments = (
            self.engineers[
                self.engineers["team_id"] == team_id
            ]
            .sort_values(["engineer_id", "capacity_rate"], ascending=[True, False])
            .drop_duplicates("engineer_id")
            .copy()
        )

        rows = []

        for candidate in team_assignments.itertuples(index=False):
            # Уже знает target skill -> обучать ему этот же skill бессмысленно.
            if candidate.engineer_id in holders:
                continue

            candidate_skills = person_skills.get(
                candidate.engineer_id,
                set(),
            )

            best = None

            for holder_id in reference_holders:
                holder_skills = set(
                    person_skills.get(holder_id, set())
                )
                holder_skills.discard(skill)

                shared_skills = sorted(
                    candidate_skills & holder_skills
                )
                profile_similarity = _jaccard(
                    candidate_skills,
                    holder_skills,
                )

                holder_role = (
                    person_meta.loc[holder_id, "role"]
                    if holder_id in person_meta.index
                    else ""
                )
                holder_grade = (
                    person_meta.loc[holder_id, "grade"]
                    if holder_id in person_meta.index
                    else ""
                )

                role_similarity = _role_similarity(
                    candidate.role,
                    holder_role,
                )
                grade_similarity = _grade_similarity(
                    candidate.grade,
                    holder_grade,
                )

                # Выбираем наиболее близкого holder-а.
                holder_fit = (
                    0.60 * profile_similarity
                    + 0.30 * role_similarity
                    + 0.10 * grade_similarity
                )

                current = {
                    "holder_id": holder_id,
                    "holder_fit": holder_fit,
                    "profile_similarity": profile_similarity,
                    "role_similarity": role_similarity,
                    "grade_similarity": grade_similarity,
                    "shared_skills": shared_skills,
                }

                if best is None or current["holder_fit"] > best["holder_fit"]:
                    best = current

            if best is None:
                continue

            same_role_with_shared_skill = (
                best["role_similarity"] >= 0.999
                and len(best["shared_skills"]) >= 1
            )

            related_role_with_shared_skills = (
                best["role_similarity"] >= 0.65
                and best["role_similarity"] < 0.999
                and len(best["shared_skills"]) >= 2
            )

            # Hard eligibility filter:
            # - та же роль: минимум 1 общий skill;
            # - смежная роль: минимум 2 общих skills.
            if not (
                same_role_with_shared_skill
                or related_role_with_shared_skills
            ):
                continue

            score = (
                0.60 * best["profile_similarity"]
                + 0.25 * best["role_similarity"]
                + 0.10 * min(float(candidate.capacity_rate), 1.0)
                + 0.05 * best["grade_similarity"]
            )

            rows.append(
                {
                    "team_id": team_id,
                    "skill": skill,
                    "engineer_id": candidate.engineer_id,
                    "role": candidate.role,
                    "grade": candidate.grade,
                    "capacity_rate": float(candidate.capacity_rate),
                    "training_score": round(float(score), 4),
                    "closest_holder": best["holder_id"],
                    "shared_skill_count": len(best["shared_skills"]),
                    "shared_skills": ", ".join(best["shared_skills"]),
                    "profile_similarity": round(
                        float(best["profile_similarity"]),
                        4,
                    ),
                    "role_similarity": round(
                        float(best["role_similarity"]),
                        4,
                    ),
                    "reason": (
                        f"closest holder={best['holder_id']}; "
                        f"same role={'yes' if best['role_similarity'] >= 0.999 else 'no'}; "
                        f"shared skills={len(best['shared_skills'])}"
                    ),
                }
            )

        if not rows:
            return pd.DataFrame(columns=columns)

        return (
            pd.DataFrame(rows)[columns]
            .sort_values(
                [
                    "training_score",
                    "shared_skill_count",
                    "capacity_rate",
                    "engineer_id",
                ],
                ascending=[False, False, False, True],
            )
            .head(top_n)
            .reset_index(drop=True)
        )

    def employee_absence_impact(
        self,
        engineer_id: str,
        team_id: str | None = None,
    ) -> pd.DataFrame:
        """
        Что произойдёт с BF, если сотрудник станет недоступен.

        Если team_id=None, анализируем все команды, куда он назначен.
        """
        assignments = self.engineers[
            self.engineers["engineer_id"] == engineer_id
        ].copy()

        if team_id is not None:
            assignments = assignments[
                assignments["team_id"] == team_id
            ]

        if assignments.empty:
            raise KeyError(
                f"Инженер {engineer_id!r} не найден "
                f"в указанной области."
            )

        skills = sorted(
            self.engineer_skills.loc[
                self.engineer_skills["engineer_id"] == engineer_id,
                "skill",
            ].unique()
        )

        rows = []

        for assignment in assignments.itertuples(index=False):
            current_team = assignment.team_id

            # Роль
            role = assignment.role
            team_before = self._team_bf(
                current_team, "role", role
            )
            org_before = self._org_bf("role", role)

            rows.append(
                {
                    "engineer_id": engineer_id,
                    "team_id": current_team,
                    "competency_type": "role",
                    "competency": role,
                    "team_bf_before": team_before,
                    "team_bf_after": max(team_before - 1, 0),
                    "organization_bf_before": org_before,
                    "organization_bf_after": max(org_before - 1, 0),
                }
            )

            # Skills
            for skill in skills:
                team_before = self._team_bf(
                    current_team, "skill", skill
                )
                org_before = self._org_bf("skill", skill)

                rows.append(
                    {
                        "engineer_id": engineer_id,
                        "team_id": current_team,
                        "competency_type": "skill",
                        "competency": skill,
                        "team_bf_before": team_before,
                        "team_bf_after": max(team_before - 1, 0),
                        "organization_bf_before": org_before,
                        "organization_bf_after": max(org_before - 1, 0),
                    }
                )

        result = pd.DataFrame(rows)
        if result.empty:
            return result

        result["becomes_uncovered"] = (
            result["team_bf_after"] == 0
        )
        result["becomes_single_owner"] = (
            result["team_bf_after"] == 1
        )

        return result.sort_values(
            [
                "becomes_uncovered",
                "becomes_single_owner",
                "competency_type",
                "competency",
            ],
            ascending=[False, False, True, True],
        ).reset_index(drop=True)
