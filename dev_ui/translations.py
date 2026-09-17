"""Russian display labels for the temporary DEV UI."""

from __future__ import annotations

from typing import Any


REASON_TITLES = {
    "UNKNOWN_TASK_DATA": "Недостаточно данных о задаче",
    "INSUFFICIENT_TEAM_CAPACITY": "Не хватает ресурса команды по Story Points",
    "INSUFFICIENT_ROLE_CAPACITY": "Не хватает часов специалистов нужной роли",
    "INSUFFICIENT_ENGINEER_CAPACITY": "Не хватает доступного времени инженеров",
    "INSUFFICIENT_SKILL_CAPACITY": "Не хватает специалистов с нужными компетенциями",
    "DEPENDENCY_NOT_COMPLETED": "Не завершена блокирующая задача",
    "UNKNOWN_DEPENDENCY_DATE": "Неизвестен срок завершения блокирующей задачи",
    "MISSING_DEPENDENCY_REFERENCE": "Не найдена блокирующая задача",
    "MULTI_SPRINT_REQUIRED": "Для выполнения требуется несколько спринтов",
    "PLANNED_IN_QUARTER": "Включено в квартальный план",
    "ALREADY_COMPLETED": "Задача завершена до начала квартала",
    "OUTSIDE_QUARTER": "Задача не помещается в шесть спринтов",
    "ALLOCATION_ROLLED_BACK": "Пробное распределение ресурсов отменено",
    "STORY_POINTS_WITHOUT_HOURS": "Нет пригодной оценки часов для распределения SP",
    "NO_AVAILABLE_ROLE_SPECIALIST": "Нет специалиста требуемой роли",
    "UNSUPPORTED_TASK_STATUS": "Статус задачи не поддерживается планировщиком",
    "TEAM_REASSIGNED": "Задача назначена другой существующей команде",
}

APP_STATUS_LABELS = {
    "PLANNED": "Запланировано",
    "IN_PROGRESS": "В работе",
    "COMPLETED": "Завершено",
    "MOVED": "Перенесено",
    "INELIGIBLE_DATA_CONSTRAINT": "Невозможно запланировать: нет специалиста",
    "CANCELLED": "Отменено",
    "UNKNOWN": "Недостаточно данных",
}

PLACEMENT_OUTCOME_LABELS = {
    "REJECTED": "Отклонена",
    "TENTATIVE": "Пробная",
    "COMMITTED": "Подтверждена",
    "ROLLED_BACK": "Отменена",
}

SHIFT_DIRECTION_LABELS = {
    "EARLIER": "Раньше baseline",
    "LATER": "Позже baseline",
    "ON_BASELINE": "По baseline",
}

RESULT_LABELS = {"OK": "Достаточно", "FAIL": "Недостаточно"}
SEVERITY_LABELS = {"WARNING": "Предупреждение", "ERROR": "Ошибка"}
ISSUE_TYPE_LABELS = {
    "DUPLICATE_TASK": "Повторяющийся task_id",
    "MISSING_TASK": "Задача отсутствует в исходной таблице Tasks",
    "ORPHAN_TASK_REFERENCE": "Ссылка на отсутствующую задачу",
    "INVALID_NUMERIC_VALUE": "Некорректное числовое значение",
}

COLUMN_LABELS = {
    "file": "Файл",
    "size_bytes": "Размер, байт",
    "modified_at": "Изменён",
    "record_type": "Тип записи",
    "initiative_id": "PRODF / инициатива",
    "task_id": "task_id",
    "parent_id": "parent_id",
    "summary": "Название",
    "team_id": "Команда / team_id",
    "original_team_id": "Исходная команда",
    "assigned_team_id": "Назначенная команда",
    "team_reassigned": "Команда изменена",
    "team_assignment_reason": "Причина смены команды",
    "status": "Статус из датасета",
    "rung": "Rung",
    "spent_time": "Затрачено часов",
    "story_points": "Story Points",
    "created_at": "Создана",
    "planned_start": "Плановое начало",
    "planned_end": "Плановое окончание",
    "actual_start": "Фактическое начало",
    "actual_end": "Фактическое окончание",
    "estimated_hh": "Оценка, ч",
    "planned_result": "Плановый результат",
    "included_in_sprint": "Включено в спринт",
    "customer": "Заказчик",
    "executor": "Исполнитель",
    "final_result": "Итоговый результат",
    "is_placeholder": "Задача-заглушка",
    "role": "Роль",
    "role_raw": "Роль в исходном Excel",
    "required_hours": "Требуется часов",
    "missing_role": "Отсутствующая роль",
    "available_specialists": "Доступно специалистов",
    "blocking_task_id": "blocking_task_id",
    "blocked_task_id": "blocked_task_id",
    "dependency_type": "dependency_type из датасета",
    "blocking_exists": "blocking_task_id существует",
    "blocked_exists": "blocked_task_id существует",
    "blocking_is_placeholder": "blocking_task_id — заглушка",
    "blocked_is_placeholder": "blocked_task_id — заглушка",
    "has_placeholder": "Есть задача-заглушка",
    "engineer_id": "engineer_id",
    "grade": "Грейд",
    "capacity_rate": "capacity_rate",
    "available_hours": "Доступно часов",
    "used_hours": "Использовано часов",
    "nominal_hours": "Номинально часов",
    "technology": "Технология / компетенция",
    "snapshot_date": "Дата снимка",
    "velocity_achieved": "Фактическая скорость, SP",
    "planned_sp": "План, SP",
    "average_historical_velocity": "Средняя историческая производительность, SP",
    "calculated_capacity": "Расчётная ёмкость, SP",
    "severity": "Уровень",
    "issue_type": "Тип проблемы",
    "entity_id": "Идентификатор",
    "source": "Источник",
    "details": "Подробности",
    "source_order": "Порядок в источнике",
    "value": "Значение",
    "completed_sprint": "Спринт завершения",
    "average_velocity_sp": "Средняя историческая производительность, SP",
    "focus_factor": "Коэффициент фокуса",
    "capacity_sp": "Доступная ёмкость, SP",
    "already_used_sp": "Уже использовано, SP",
    "used_sp": "Использовано SP",
    "available_sp": "Доступно SP",
    "required_sp": "Требуется SP",
    "deficit_sp": "Дефицит SP",
    "required_technologies": "Требуемые компетенции",
    "matching_engineer_ids": "Подходящие инженеры",
    "deficit_hours": "Дефицит часов",
    "remaining_hours_after_sprint": "Останется часов после спринта",
    "result": "Результат",
    "teams": "Команды",
    "task_team": "Команда задачи",
    "cross_team": "Привлечение инженера",
    "roles": "Роли",
    "skills": "Компетенции",
    "required_role": "Требуемая роль",
    "matches_required_skills": "Компетенции подходят",
    "earliest_start_sprint": "Самый ранний спринт старта",
    "code": "Код причины",
    "description": "Описание",
    "plan_status": "Статус в плане",
    "source_status": "Статус из датасета",
    "priority_value": "Rung из датасета",
    "source_rung": "Rung из датасета",
    "total_score": "Score",
    "base_score": "Базовый score",
    "dynamic_score": "Динамический score",
    "rung_score": "Вклад rung",
    "status_score": "Бонус статуса",
    "deadline_score": "Бонус срочности",
    "initiative_completion_score": "Бонус завершения инициативы",
    "dependency_unlock_score": "Бонус разблокировки зависимостей",
    "scoring_version": "Версия scoring",
    "priority_order": "Порядок рассмотрения",
    "priority_explanation": "Правило порядка",
    "start_sprint": "Спринт старта",
    "completion_sprint": "Спринт завершения",
    "is_multi_sprint": "Несколько спринтов",
    "sprint_number": "Спринт",
    "allocated_hours": "Выделено часов",
    "task_story_points": "Полный SP задачи",
    "capacity_sp_load": "SP-нагрузка команды",
    "planned_completion_sp": "Плановое завершение, SP",
    "story_points_charged": "Завершено SP",
    "completed_sp": "Завершено SP",
    "team_sp_consumed": "Учтено SP команды",
    "hours": "Часы",
    "remaining_sp": "Осталось SP",
    "allocation_state": "Этап задачи",
    "reason_codes": "Коды причин",
    "reason_descriptions": "Причины",
    "baseline_start_date": "Baseline: начало",
    "baseline_end_date": "Baseline: окончание",
    "forecast_start_date": "Forecast: начало",
    "forecast_end_date": "Forecast: окончание",
    "start_shift_days": "Сдвиг начала, дней",
    "end_shift_days": "Сдвиг окончания, дней",
    "start_shift_direction": "Направление сдвига начала",
    "end_shift_direction": "Направление сдвига окончания",
    "outside_quarter": "Риск выхода за квартал",
    "outcome": "Результат попытки",
    "allocated_story_points": "Завершено SP",
}

SKILL_DATA_LIMITATION_RU = (
    "В processed-данных нет связи между задачей и требуемыми технологиями. "
    "Проверка компетенций выполняется только при передаче отдельного справочника требований."
)


def _number(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def reason_details_ru(code: str, details: dict[str, Any]) -> str:
    """Render reason details in Russian without changing the machine-readable payload."""

    if code == "UNKNOWN_TASK_DATA":
        missing = details.get("missing_fields")
        if missing:
            return f"Не заполнены обязательные поля: {', '.join(map(str, missing))}."
        return f"Для задачи {details.get('task_id', '')} недостаточно исходных данных."
    if code == "INSUFFICIENT_TEAM_CAPACITY":
        return (
            f"Команда {details.get('team_id', '')}: требуется {_number(details.get('required_sp'))} SP, "
            f"доступно {_number(details.get('available_sp'))} SP, "
            f"дефицит {_number(details.get('deficit_sp'))} SP."
        )
    if code == "INSUFFICIENT_ROLE_CAPACITY":
        return (
            f"Роль «{details.get('role', '')}»: требуется "
            f"{_number(details.get('required_hours'))} ч, доступно "
            f"{_number(details.get('available_hours'))} ч, дефицит "
            f"{_number(details.get('deficit_hours'))} ч."
        )
    if code == "INSUFFICIENT_ENGINEER_CAPACITY":
        return (
            f"Роль «{details.get('role', '')}»: после учёта текущей загрузки доступно "
            f"{_number(details.get('available_hours'))} ч из "
            f"{_number(details.get('required_hours'))} ч."
        )
    if code == "INSUFFICIENT_SKILL_CAPACITY":
        technologies = ", ".join(map(str, details.get("required_technologies", [])))
        return (
            f"Роль «{details.get('role', '')}», компетенции: {technologies}. "
            f"Требуется {_number(details.get('required_hours'))} ч, доступно "
            f"{_number(details.get('available_matching_hours'))} ч."
        )
    if code == "NO_AVAILABLE_ROLE_SPECIALIST":
        return (
            f"Роль «{details.get('role', '')}»: требуется "
            f"{_number(details.get('required_hours'))} ч, специалистов в профилях — "
            f"{_number(details.get('available_specialists'))}."
        )
    if code == "DEPENDENCY_NOT_COMPLETED":
        blocker = details.get("blocking_task_id", "")
        completed = details.get("completed_sprint")
        if completed is None:
            return f"Блокирующая задача {blocker} не отмечена завершённой."
        return (
            f"Задача {blocker} завершена в спринте {completed}; зависимую задачу можно начать "
            f"не раньше спринта {details.get('earliest_start_sprint')}."
        )
    if code == "UNKNOWN_DEPENDENCY_DATE":
        return (
            f"Для блокирующей задачи {details.get('blocking_task_id', '')} "
            "неизвестен спринт завершения."
        )
    if code == "MISSING_DEPENDENCY_REFERENCE":
        return (
            f"Блокирующая задача {details.get('blocking_task_id', '')} отсутствует "
            "в нормализованной таблице tasks."
        )
    if code == "MULTI_SPRINT_REQUIRED":
        if details.get("sprint_count") is not None:
            text = (
                f"Задача распределена на {details.get('sprint_count')} спринта: "
                f"с {details.get('start_sprint')} по {details.get('completion_sprint')}."
            )
            allocations = details.get("allocations") or []
            if allocations:
                parts = [
                    f"Sprint {item.get('sprint_number')}: "
                    f"{_number(item.get('allocated_hours'))} ч / "
                    f"SP-нагрузка {_number(item.get('capacity_sp_load'))} / "
                    f"завершено {_number(item.get('completed_sp'))} SP"
                    for item in allocations
                ]
                text += " " + "; ".join(parts) + "."
            return text
        return (
            f"По роли «{details.get('role', '')}» после спринта останется "
            f"{_number(details.get('remaining_hours'))} ч."
        )
    if code == "PLANNED_IN_QUARTER":
        return (
            f"Задача запланирована со спринта {details.get('start_sprint')} "
            f"по спринт {details.get('completion_sprint')}."
        )
    if code == "ALREADY_COMPLETED":
        return "В исходных данных задача имеет статус Done и считается завершённой до квартала."
    if code == "OUTSIDE_QUARTER":
        return (
            f"Самое раннее возможное завершение — спринт "
            f"{details.get('forecast_completion_sprint_minimum', '')}, то есть за пределами "
            "шести спринтов."
        )
    if code == "ALLOCATION_ROLLED_BACK":
        return (
            f"Пробное размещение отменено: освобождено "
            f"{_number(details.get('rolled_back_hours'))} ч и "
            f"{_number(details.get('rolled_back_capacity_sp_load'))} SP-нагрузки."
        )
    if code == "STORY_POINTS_WITHOUT_HOURS":
        return (
            f"У задачи есть {_number(details.get('story_points'))} SP, но нет положительной "
            "оценки часов. Целочисленная SP-нагрузка распределяется без выдумывания часов."
        )
    if code == "UNSUPPORTED_TASK_STATUS":
        return f"Исходный статус задачи не поддерживается: {details.get('source_status', '')}."
    return str(details)
